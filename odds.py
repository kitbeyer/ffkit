import json
import requests
import math
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytz


API_KEY = '09f2a0ceb1c2da0a1a913f0b4dc121a7'
SPORT = 'americanfootball_nfl'  # use the sport_key from the /sports endpoint below, or use 'upcoming' to see the next 8 games across all sports
REGIONS = 'us'  # uk | us | eu | au. Multiple can be specified if comma delimited
MARKETS = 'totals,spreads'  # h2h | spreads | totals. Multiple can be specified if comma delimited
ODDS_FORMAT = 'decimal'  # decimal | american
DATE_FORMAT = 'iso'  # iso | unix

HOST_URL = 'https://api.the-odds-api.com/'
SPORTS_ENDPOINT = 'v4/sports/'
ODDS_ENDPOINT = f'v4/sports/{SPORT}/odds/'

DATA_DIR = Path('/Users/kitbeyer/data/ffkit')
SPORTS_FILE = 'sports.json'
ODDS_FILE = 'odds.json'

GAMES_FILE = 'games.csv'

def update_sports(outpath):

    # GET sports

    request_url = HOST_URL + SPORTS_ENDPOINT

    parameters = {
        'apiKey': API_KEY
    }

    response = requests.get(url=request_url, params=parameters)

    if response.status_code != 200:
        print(f'Failed to get sports: status_code {response.status_code}, response body {response.text}')
    else:
        with open(outpath, 'w') as outfile:
            json.dump(response.json(), outfile, sort_keys=True, indent=4)


def update_odds(outpath):

    # GET odds

    request_url = HOST_URL + ODDS_ENDPOINT

    parameters = {
        'apiKey': API_KEY,
        'regions': REGIONS,
        'markets': MARKETS,
        'oddsFormat': ODDS_FORMAT,
        'dateFormat': DATE_FORMAT
    }

    response = requests.get(url=request_url, params=parameters)

    if response.status_code != 200:
        print(f'Failed to get odds: status_code {response.status_code}, response body {response.text}')
    else:

        with open(outpath, 'w') as outfile:
            json.dump(response.json(), outfile, sort_keys=True, indent=4)

        # Check the usage quota
        print('Remaining requests:', response.headers['x-requests-remaining'])
        print('Used requests:', response.headers['x-requests-used'])

if __name__ == "__main__":

    is_update_sports = True
    is_update_odds = True

    timezone = pytz.timezone('US/Eastern')

    today = date.today()
    subtract_days = today.weekday() - 1
    subtract_days = subtract_days if subtract_days >= 0 else subtract_days + 7

    week_start = today - timedelta(days=subtract_days)
    week_start = datetime.combine(week_start, datetime.min.time())
    week_start = timezone.localize(week_start)
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)

    quiet = False

    sports_path = DATA_DIR / SPORTS_FILE
    odds_path = DATA_DIR / ODDS_FILE
    games_path = DATA_DIR / GAMES_FILE

    if is_update_sports:
        update_sports(sports_path)
    if is_update_odds:
        update_odds(odds_path)

    with open(odds_path, 'r') as outfile:
        odds = json.load(outfile)

    games = pd.json_normalize(odds)
    games = games[['id', 'commence_time', 'home_team', 'away_team']]
    games = games.rename(columns={'id': 'game_id'})

    game_books = pd.json_normalize(odds, record_path=['bookmakers'], meta=['id', 'home_team', 'away_team'])

    total = []
    home_spread = []
    away_spread = []

    for i, book in game_books.iterrows():
        outcomes_df = pd.json_normalize(book['markets'], record_path=['outcomes'])

        book_total = outcomes_df.loc[outcomes_df['name'] == 'Over', 'point'].to_numpy()
        book_total = book_total[0] if book_total.size > 0 else None
        total.append(book_total)

        book_home_spread = outcomes_df.loc[outcomes_df['name'] == book['home_team'], 'point'].to_numpy()
        book_home_spread = book_home_spread[0] if book_home_spread.size > 0 else None
        home_spread.append(book_home_spread)

        book_away_spread = outcomes_df.loc[outcomes_df['name'] == book['away_team'], 'point'].to_numpy()
        book_away_spread = book_away_spread[0] if book_away_spread.size > 0 else None
        away_spread.append(book_away_spread)

    game_books['total'] = total
    game_books['home_spread'] = home_spread
    game_books['away_spread'] = away_spread

    game_books = game_books[['id', 'key', 'last_update', 'total', 'home_spread', 'away_spread']]
    game_books = game_books.rename(columns={'id': 'game_id', 'key': 'bookmaker'})

    total = []
    home_spread = []

    for i, game in games.iterrows():

        cur_game_books = game_books.loc[game_books['game_id'] == game['game_id']]
        line_counts = cur_game_books.groupby(['total', 'home_spread'], as_index=False, dropna=False).count()
        line_counts = line_counts[['total', 'home_spread', 'game_id']]
        line_counts = line_counts.rename(columns={'game_id': 'count'})

        game_total = line_counts.iloc[line_counts['count'].idxmax()]['total'] if not line_counts.empty else math.nan
        game_home_spread = line_counts.iloc[line_counts['count'].idxmax()]['home_spread'] if not line_counts.empty else math.nan


        total.append(game_total)
        home_spread.append(game_home_spread)

    games['total'] = total
    games['home_spread'] = home_spread

    games['implied_home_score'] = (games['total'] - games['home_spread']) / 2
    games['implied_away_score'] = (games['total'] + games['home_spread']) / 2

    games['commence_time'] = pd.to_datetime(games['commence_time'], infer_datetime_format=True, utc=True)
    games['commence_time'] = games['commence_time'].dt.tz_convert(timezone)

    games_week = games[(games['commence_time'] > week_start) & (games['commence_time'] < week_end)]

    teams_week_home = games_week.rename(columns={'home_team': 'team',
                                                 'away_team': 'opponent',
                                                 'implied_home_score': 'implied_pf',
                                                 'implied_away_score': 'implied_pa'})

    teams_week_away = games_week.rename(columns={'away_team': 'team',
                                                 'home_team': 'opponent',
                                                 'implied_away_score': 'implied_pf',
                                                 'implied_home_score': 'implied_pa'})

    teams_week_away['opponent'] = '@' + teams_week_away['opponent']

    teams_week = pd.concat([teams_week_home, teams_week_away])

    teams_week = teams_week[['team', 'opponent', 'commence_time', 'implied_pf', 'implied_pa', 'game_id']]
    teams_week = teams_week.sort_values(by='implied_pf', ascending=False)

    teams_week.to_csv(games_path, index=False)

    if not quiet:
        print(teams_week[['team', 'opponent', 'commence_time', 'implied_pf', 'implied_pa']])

