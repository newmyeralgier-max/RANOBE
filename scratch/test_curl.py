import subprocess
import time

def fetch(url):
    r = subprocess.run(
        ["curl", "-v", "-L", "-m", "20",
         "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
         url],
        capture_output=True, encoding='utf-8', errors='replace'
    )
    return r.stdout, r.stderr

# Need to find the URL for Ch 235 first.
# I'll fetch the chapter list for the novel to find the link.
novel_id = "1207051"
url = f"https://ranobes.net/chapters/{novel_id}/page/1/"
html, err = fetch(url)
print(html[:500])
print(err)
