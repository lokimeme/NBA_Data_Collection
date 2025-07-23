import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import json
import re
import io

# --- Configuration ---
# Years for trend lines (e.g., Pace, Shot Selection)
# We'll scrape every other year to speed things up.
YEARS_FOR_TRENDS = range(2004, 2025, 2) 

# Years for the detailed player salary vs. shot distance chart
PLAYER_DATA_YEARS = [2004, 2023]

# The most recent year to use for "all-time" shot efficiency data
EFFICIENCY_YEAR = 2024

# --- Helper Function to Scrape and Parse a Page ---

def get_soup_from_url(url):
    """
    Fetches a webpage and returns a BeautifulSoup object.
    It also handles the specific case of Basketball-Reference where tables are hidden in comments.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, headers=headers)
        response.raise_for_status() # Raises an exception for bad status codes (4xx or 5xx)
        time.sleep(3) # Be a good internet citizen and wait between requests
        
        # Basketball-Reference wraps tables in comments, so we need to uncomment them
        comment = re.compile("<!--|-->")
        soup = BeautifulSoup(re.sub(comment, "", response.text), 'lxml')
        return soup
    except requests.exceptions.RequestException as e:
        print(f"Error fetching {url}: {e}")
        return None

# --- Scraping Functions for Each Data Module ---

def find_table_by_caption(soup, caption_text):
    """Finds a table based on the text in its caption."""
    for table in soup.find_all('table'):
        if table.caption and caption_text in table.caption.get_text():
            return table
    return None

def scrape_league_trends(years):
    """
    Scrapes league-wide data for Pace, Offensive Rating, and shot selection percentages.
    """
    print("Scraping league-wide trend data...")
    trends = {
        "seasons": [], "threePAr": [], "midRangeAr": [], "atRimAr": [], "pace": [], "oRtg": []
    }
    
    for year in years:
        url_shooting = f"https://www.basketball-reference.com/leagues/NBA_{year}_shooting.html"
        soup_shooting = get_soup_from_url(url_shooting)
        if soup_shooting:
            # FIX: Find table by its ID, which is more reliable
            shooting_table = soup_shooting.find("table", id="team-shooting")
            if shooting_table:
                all_rows = shooting_table.find_all("tr")
                if all_rows:
                    lg_avg_row = all_rows[-1]
                    try:
                        at_rim_pct = float(lg_avg_row.find("td", {"data-stat": "fga_pct_0_3"}).text)
                        mid_range_pct = float(lg_avg_row.find("td", {"data-stat": "fga_pct_10_16"}).text) + \
                                        float(lg_avg_row.find("td", {"data-stat": "fga_pct_16_3p"}).text)
                        three_pt_pct = float(lg_avg_row.find("td", {"data-stat": "fga_pct_3p"}).text)
                        
                        trends["atRimAr"].append(round(at_rim_pct, 3))
                        trends["midRangeAr"].append(round(mid_range_pct, 3))
                        trends["threePAr"].append(round(three_pt_pct, 3))
                        
                        # Also grab pace and oRtg from here if available
                        pace = float(lg_avg_row.find("td", {"data-stat": "pace"}).text)
                        ortg = float(lg_avg_row.find("td", {"data-stat": "off_rtg"}).text)
                        trends["pace"].append(pace)
                        trends["oRtg"].append(ortg)
                        trends["seasons"].append(str(year))

                    except (AttributeError, ValueError) as e:
                        print(f"Warning: Could not parse all trend data for {year}: {e}")
                else:
                    print(f"Warning: No rows found in team-shooting table for {year}")
            else:
                print(f"Warning: Could not find team-shooting table for {year}")

    return trends

def scrape_shot_efficiency(year):
    """
    Scrapes shooting percentages by distance to calculate Points Per Shot (PPS).
    """
    print(f"Scraping shot efficiency data for {year}...")
    url = f"https://www.basketball-reference.com/leagues/NBA_{year}_shooting.html"
    soup = get_soup_from_url(url)
    if not soup: return {}

    shooting_table = soup.find("table", id="team-shooting")
    if not shooting_table:
        print(f"Warning: Could not find team-shooting table for {year}.")
        return {}
        
    lg_avg_row = shooting_table.find_all("tr")[-1]

    try:
        pps_at_rim = float(lg_avg_row.find("td", {"data-stat": "fg_pct_0_3"}).text) * 2
        pps_5_9 = float(lg_avg_row.find("td", {"data-stat": "fg_pct_3_10"}).text) * 2
        pps_10_14 = float(lg_avg_row.find("td", {"data-stat": "fg_pct_10_16"}).text) * 2
        pps_15_19 = float(lg_avg_row.find("td", {"data-stat": "fg_pct_16_3p"}).text) * 2
        pps_3p = float(lg_avg_row.find("td", {"data-stat": "fg_pct_3p"}).text) * 3

        return {
            "distances": ['< 5 ft', '5-9 ft', '10-14 ft', '15-19 ft', '20-24 ft', '25-29 ft'],
            "pps_all_time": [ round(d, 2) for d in [pps_at_rim, pps_5_9, pps_10_14, pps_15_19, pps_3p, pps_3p] ]
        }
    except (AttributeError, ValueError) as e:
        print(f"Warning: Could not parse shot efficiency data for {year}: {e}")
        return {}

def scrape_player_data(year):
    """
    Scrapes player salaries and average shot distance for a given year by going team-by-team.
    """
    print(f"Scraping player salary and shooting data for {year}...")
    
    # --- Part 1: Scrape Salaries by looping through each team ---
    all_teams_url = f"https://www.basketball-reference.com/leagues/NBA_{year}.html"
    soup = get_soup_from_url(all_teams_url)
    if not soup: return pd.DataFrame()

    # FIX: More robust selector for team links that works for old and new layouts
    team_links = soup.select('table.stats_table a[href^="/teams/"]')
    team_abbrs = list(set([link['href'].split('/')[2] for link in team_links]))
    
    all_salaries_df = []
    print(f"Found {len(team_abbrs)} teams for {year}. Scraping salaries for each...")

    for team in team_abbrs:
        team_url = f"https://www.basketball-reference.com/teams/{team}/{year}.html"
        team_soup = get_soup_from_url(team_url)
        if team_soup:
            try:
                salary_table = team_soup.find("table", id="salaries2")
                if salary_table:
                    # FIX: Address FutureWarning by using io.StringIO
                    df = pd.read_html(io.StringIO(str(salary_table)))[0]
                    df = df[['Player', 'Salary']]
                    all_salaries_df.append(df)
                else:
                    print(f"Warning: Could not find salary table for {team} in {year}")
            except Exception as e:
                print(f"Error processing salaries for {team} in {year}: {e}")
    
    if not all_salaries_df:
        print(f"Error: Could not scrape any salary data for {year}.")
        return pd.DataFrame()

    salaries_df = pd.concat(all_salaries_df, ignore_index=True)
    salaries_df.drop_duplicates(subset=['Player'], inplace=True)
    salaries_df['Salary'] = salaries_df['Salary'].replace({r'\$': '', r',': ''}, regex=True).astype(float) / 1_000_000
    
    # --- Part 2: Scrape Shooting Data ---
    shooting_url = f"https://www.basketball-reference.com/leagues/NBA_{year}_shooting.html"
    shooting_soup = get_soup_from_url(shooting_url)
    if not shooting_soup: return pd.DataFrame()
        
    shooting_table = shooting_soup.find("table", id="shooting")
    if not shooting_table:
        print(f"Warning: Could not find player shooting table for {year}.")
        return pd.DataFrame()

    shooting_df = pd.read_html(io.StringIO(str(shooting_table)))[0]
    shooting_df.columns = shooting_df.columns.droplevel()
    shooting_df = shooting_df[['Player', 'Dist.']]
    shooting_df.columns = ['Player', 'avgShotDistance']
    
    # --- Part 3: Clean and Merge ---
    salaries_df['Player'] = salaries_df['Player'].str.replace('*', '', regex=False)
    shooting_df['Player'] = shooting_df['Player'].str.replace('*', '', regex=False)
    shooting_df['avgShotDistance'] = pd.to_numeric(shooting_df['avgShotDistance'], errors='coerce')
    shooting_df.dropna(inplace=True)

    merged_df = pd.merge(salaries_df, shooting_df, on="Player", how="inner")
    
    return merged_df.nlargest(100, 'Salary')

# --- Main Execution Block ---
if __name__ == "__main__":
    print("Starting NBAlytics data scraping process...")

    league_trends_data = scrape_league_trends(YEARS_FOR_TRENDS)
    shot_efficiency_data = scrape_shot_efficiency(EFFICIENCY_YEAR)
    
    player_data_2004_df = scrape_player_data(2004)
    player_data_2023_df = scrape_player_data(2023)

    player_2004_records = []
    if not player_data_2004_df.empty:
        inflation_multiplier = 304.702 / 188.9
        player_data_2004_df['Salary'] = (player_data_2004_df['Salary'] * inflation_multiplier).round(1)
        player_2004_records = player_data_2004_df.to_dict('records')

    player_2023_records = []
    if not player_data_2023_df.empty:
        player_2023_records = player_data_2023_df.to_dict('records')

    final_data_structure = {
        "leagueAveragesData": league_trends_data,
        "shotEfficiencyData": shot_efficiency_data,
        "playerShotDistanceData": {
            '2004': { 'data': player_2004_records, 'color': '#60a5fa' },
            '2023': { 'data': player_2023_records, 'color': '#34d399' }
        }
    }

    output_filename = "nba_data.json"
    with open(output_filename, "w") as f:
        json.dump(final_data_structure, f, indent=4)

    print(f"\nScraping complete!")
    print(f"All data has been saved to '{output_filename}'.")
    print("Upload this file along with your index.html to see the live data.")

