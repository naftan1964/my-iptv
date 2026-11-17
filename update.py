import urllib.request
import urllib.error
import concurrent.futures

USER_AGENT = "Mozilla/5.0"
TIMEOUT = 6

def read_lines(filename):
    with open(filename, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]

def check_stream(item):
    info, url = item
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status == 200:
                return (info, url)
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
                    name = current_info.split(",")[-1].lower()
                    if any(w in name for w in wanted):
                        candidates.append((current_info, line))
                    current_info = ""
        except Exception as e:
            print(f"Ошибка загрузки {src}: {e}")

    print(f"Найдено кандидатов: {len(candidates)}. Проверка на 429 и доступность...")

    valid_streams = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for res in executor.map(check_stream, candidates):
            if res:
                valid_streams.append(res)

    with open("my_playlist.m3u", "w", encoding="utf-8") as out:
        out.write('#EXTM3U url-tvg="https://githubusercontent.com"\n')
        for info, url in valid_streams:
            out.write(f"{info}\n{url}\n")

    print(f"Готово! Рабочих потоков сохранено: {len(valid_streams)}")

if __name__ == "__main__":
    main()
