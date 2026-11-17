import urllib.request
import concurrent.futures
import re

USER_AGENT = "Mozilla/5.0"
TIMEOUT = 6
MAX_STREAMS_PER_CHANNEL = 4

def read_sources(filename):
    with open(filename, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]

def read_grouped_channels(filename):
    channels = []
    current_group = "Общие"
    with open(filename, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current_group = line[1:-1].strip()
            else:
                channels.append((line.lower(), current_group))
    return channels

def has_timezone_shift(name):
    return bool(re.search(r'[\s\(\[][\+\-]\d{1,2}[\)\]]?\b', name))

def set_group_title(extinf_line, new_group):
    if 'group-title="' in extinf_line:
        return re.sub(r'group-title="[^"]*"', f'group-title="{new_group}"', extinf_line)
    else:
        return extinf_line.replace("#EXTINF:-1", f'#EXTINF:-1 group-title="{new_group}"', 1)

def check_stream(item):
    key, group, info, url = item
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status == 200:
                return (key, group, info, url)
    except Exception:
        pass
    return None

def main():
    sources = read_sources("sources.txt")
    wanted_list = read_grouped_channels("channels.txt")
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
                        for w, grp in wanted_list:
                            if w in name_lower:
                                clean_info = set_group_title(current_info, grp)
                                candidates.append((w, grp, clean_info, line))
                                break
                    current_info = ""
        except Exception as e:
            print(f"Ошибка загрузки {src}: {e}")

    counts = {w: 0 for w, _ in wanted_list}
    valid_by_group = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for res in executor.map(check_stream, candidates):
            if res:
                key, grp, info, url = res
                if counts[key] < MAX_STREAMS_PER_CHANNEL:
                    valid_by_group.setdefault(grp, []).append((info, url))
                    counts[key] += 1

    with open("my_playlist.m3u", "w", encoding="utf-8") as out:
        out.write('#EXTM3U url-tvg="https://githubusercontent.com"\n')
        for _, grp in wanted_list:
            if grp in valid_by_group:
                for info, url in valid_by_group.pop(grp):
                    out.write(f"{info}\n{url}\n")

    print("Сборка и сортировка по группам завершены!")

if __name__ == "__main__":
    main()


