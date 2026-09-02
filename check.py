import requests


def search(query: str) -> str:
    """Search for information."""
    try:
        url = "https://en.wikipedia.org/w/api.php"

        params = {
            "action": "query",
            "titles": query,
            "prop": "extracts",
            "exintro": True,
            "explaintext": True,
            "format": "json",
            "formatversion": 2
        }

        headers = {
            "User-Agent": "MyWikipediaApp/1.0 (sriya13121@gmail.com)"
        }

        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        
        # ['query']['pages'][0]['extract']
        return response.json()
    
    except Exception as err:
        raise RuntimeError(f"Wikipedia search failed: {err}") from err
    
    
def weather_function(desired_location):
    """use this tool to get the current weather of a location."""

    API_KEY = "9b9a4868356344b1853160351263007"
    LOCATION = desired_location

    url = "https://api.weatherapi.com/v1/current.json"

    params = {
        "key": API_KEY,
        "q": LOCATION
    }

    response = requests.get(url, params=params)
    response.raise_for_status()

    data = response.json()
    return data['current']
print(search("OpenAI partners with AMD"))