#' Scrape data from multiple sources and multiple positions
#'
#' This function scrapes data from multiple sources and multiple positions and
#' returns a list of \link{tibble}s with the results. Results contain raw data
#' from the sources.
#'
#' @param src the sources that data should be scraped from should be one or more
#' of \code{c("CBS", "ESPN", "FantasyData", "FantasyPros", "FantasySharks",
#' "FFToday", "FleaFlicker", "NumberFire", "Yahoo", "FantasyFootballNerd", "NFL",
#' "RTSports","Walterfootball")}
#' @param pos the posistions that data should be scraped for. Should be one or more
#' of \code{c("QB", "RB", "WR", "TE", "K", "DST", "DL", "LB", "DB")}
#' @param season The seaon for which data should be scraped. Should be set to the
#' current season.
#' @param week The week for which data should be scraped. Set to 0 to get season
#' data.
#' @export
scrape_projections <- function(src = "FantasyPros", pos = c("QB", "RB", "WR", "TE", "K", "DST"), season = 2019, week = 0){

  pos <- match.arg(pos, several.ok = TRUE)

  names(pos) <- pos
  
  src_data <- map(pos, ~ map(projection_sources[src], ~ .x))
  src_data <- transpose(src_data)
  src_data <- map(src_data, ~ imap(.x, ~ scrape_source(.x, season, week, .y)))
  src_data <- transpose(src_data)
  src_data <- map(src_data, discard, is.null)
  src_data <- map(src_data, bind_rows, .id = "data_src")

  cat("Scraping", position, "projections from \n", src$get_url(season, week, position), "\n")
  src_res <- switch(src_type,
                    "html_source" = src$open_session(season, week, position)$scrape(),
                    src$scrape(season, week, position))
  return(src_res)


  attr(src_data, "season") <- season
  attr(src_data, "week") <- week

  return(src_data)
}

#' Scrape data for a specific position from a single source
#' @export
scrape_source <- function(src, season, week, position){
  src_type <- intersect(c("html_source", "json_source", "xlsx_source"), class(src))
  cat("Scraping", position, "projections from \n", src$get_url(season, week, position), "\n")
  src_res <- switch(src_type,
                    "html_source" = src$open_session(season, week, position)$scrape(),
                    src$scrape(season, week, position))
  return(src_res)
}
