import json
import requests
import webbrowser
import urllib

USER_NAME = 'kitshicker'
USER_ID = '469286328278708224'
SPORT = 'nfl'
SEASON = '2026'
# LEAGUE_ID = '664128514320056320'    # Gridiron Gang Dynasty
LEAGUE_ID = '1322935618086379520'   # Beyer Dynasty Best Ball

HOST_URL = 'https://api.sleeper.app/'



def get_user_info(user):

    request_url = HOST_URL + f'v1/user/{user}'

    response = requests.get(url=request_url)

    return response.json()

def get_user_leagues(user_id, sport, season):

    request_url = HOST_URL + f'v1/user/{user_id}/leagues/{sport}/{season}'

    response = requests.get(url=request_url)

    return response.json()

def get_league(league_id):

    request_url = HOST_URL + f'v1/league/{league_id}'

    response = requests.get(url=request_url)

    return response.json()

def get_league_rosters(league_id):

    request_url = HOST_URL + f'v1/league/{league_id}/rosters'

    response = requests.get(url=request_url)

    return response.json()

def get_league_users(league_id):

    request_url = HOST_URL + f'v1/league/{league_id}/users'

    response = requests.get(url=request_url)

    return response.json()

def get_league_matchups(league_id, week):
    
    request_url = HOST_URL + f'v1/league/{league_id}/matchups/{week}'
    
    response = requests.get(url=request_url)
    
    return response.json()

#user_json = get_user_info('kitshicker')
#user_leagues = get_user_leagues(user_id=USER_ID, sport=SPORT, season=SEASON)
#league_json = get_league(LEAGUE_ID)
#rosters_json = get_league_rosters(LEAGUE_ID)
# league_users_json = get_league_users(LEAGUE_ID)
league_matchups_json = get_league_matchups(LEAGUE_ID, 11)

print(json.dumps(league_matchups_json, indent=4))


