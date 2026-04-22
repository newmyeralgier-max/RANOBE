import urllib.request
import time

url = "https://ranobes.net/chapters/infinite-save-infinite-reload-1207051/3080630/"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
}

def test():
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            content = resp.read().decode('utf-8', errors='replace')
            print(f"Content length: {len(content)}")
            if "Just a moment" in content:
                print("Blocked by Cloudflare/Turnstile")
            else:
                print("Success! First 500 chars:")
                print(content[:500])
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test()
