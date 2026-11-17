import urllib.request
import concurrent.futures
import re

USER_AGENT = "Mozilla/5.0"
TIMEOUT = 6
MAX_STREAMS_PER_CHANNEL = 4

def read_lines(filename):
    with open(filename, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]

def has_timezone_shift(name):
    return bool(re.search(r'[\s\(\[][\+\-]\d{1,2}[\)\]]?\b', name))

def check_stream(item):
    key, info, url = item
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status == 200:
                return (key, info, url)
    except Exception:
        pass
    return None

def main():
    sources = read_lines("sources.txt")
    wanted = [c.lower() for c in read_lines("channels.txt")]
    candidates = []

    for src in sources:
        try:
            req = urllib.request.Request(src, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as resp:
                lines = resp.read().decode("utf-8", errors="ignore").splitlines()
            
            current_info = ""
            for line in lines:
                line = line.strip()
                if line.startswith("#EXTINF"):
                    current_info = line
                elif line.startswith("http") and current_info:
                    raw_name = current_info.split(",")[-1].strip()
                    if not has_timezone_shift(raw_name):
                        name_lower = raw_name.lower()
                        for w in wanted:
                            if w in name_lower:
                                candidates.append((w, current_info, line))
                                break
                    current_info = ""
        except Exception as e:
            print(f"Ошибка загрузки {src}: {e}")

    print(f"Кандидатов без часовых поясов: {len(candidates)}. Проверка...")

    counts = {w: 0 for w in wanted}
    valid_streams = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for res in executor.map(check_stream, candidates):
            if res:
                key, info, url = res
                if counts[key] < MAX_STREAMS_PER_CHANNEL:
                    valid_streams.append((info, url))
                    counts[key] += 1

    with open("my_playlist.m3u", "w", encoding="utf-8") as out:
        out.write('#EXTM3U url-tvg="https://githubusercontent.com"\n')
        for info, url in valid_streams:
            out.write(f"{info}\n{url}\n")

    print(f"Готово! Сохранено рабочих потоков: {len(valid_streams)}")

if __name__ == "__main__":
    main()

