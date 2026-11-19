import urllib.request
import concurrent.futures
import re

USER_AGENT = "Mozilla/5.0"
TIMEOUT = 6
MAX_STREAMS_PER_CHANNEL = 3

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

def is_channel_match(wanted, channel_name):
    pattern = r'(?:^|[^а-яёa-z0-9])' + re.escape(wanted) + r'(?:$|[^а-яёa-z0-9])'
    return bool(re.search(pattern, channel_name))

def get_quality_score(name):
    name_low = name.lower()
    if re.search(r'\b(full\s*hd|fhd|1080p?|50\s*fps|orig|4k|uhd)\b', name_low):
        return 1
    if re.search(r'\b(hd|720p?)\b', name_low):
        return 2
    if re.search(r'\bsd\b', name_low):
        return 4
    return 3

def check_stream(item):
    idx, key, group, info, url = item
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status == 200:
                return (idx, key, group, info, url)
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
                        for idx, (w, grp) in enumerate(wanted_list):
                            if is_channel_match(w, name_lower):
                                candidates.append((idx, w, grp, current_info, line))
                                break
                    current_info = ""
        except Exception as e:
            print(f"Ошибка загрузки {src}: {e}")

    # Сортируем кандидатов: сначала по порядку каналов в списке, затем HD наверх
    candidates.sort(key=lambda x: (x[0], get_quality_score(x[3].split(",")[-1])))

    valid_by_idx = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for res in executor.map(check_stream, candidates):
            if res:
                idx, key, grp, info, url = res
                bucket = valid_by_idx.setdefault(idx, [])
                if len(bucket) < MAX_STREAMS_PER_CHANNEL:
                    bucket.append((grp, info, url))

    seen_names = {}
    epg_url = "https://raw.githubusercontent.com/Lorax121/epg_v2/main/data/epg2.xml.gz"

    with open("my_playlist.m3u", "w", encoding="utf-8") as out:
        out.write(f'#EXTM3U url-tvg="{epg_url}"\n')
        for idx, (w, grp) in enumerate(wanted_list):
            if idx in valid_by_idx:
                streams = valid_by_idx[idx]
                best_id = ""
                best_logo = ""
                for _, s_info, _ in streams:
                    if not best_id:
                        m_id = re.search(r'tvg-id="([^"]+)"', s_info)
                        if m_id:
                            best_id = m_id.group(1)
                    if not best_logo:
                        m_logo = re.search(r'tvg-logo="([^"]+)"', s_info)
                        if m_logo:
                            best_logo = m_logo.group(1)

                for group_name, info, url in streams:
                    raw_name = info.split(",").pop().strip()
                    name_key = raw_name.lower()
                    seen_names[name_key] = seen_names.get(name_key, 0) + 1
                    final_name = f"{raw_name} ({seen_names[name_key]})" if seen_names[name_key] > 1 else raw_name

                    m_id = re.search(r'tvg-id="([^"]+)"', info)
                    m_logo = re.search(r'tvg-logo="([^"]+)"', info)
                    cur_id = m_id.group(1) if m_id else best_id
                    cur_logo = m_logo.group(1) if m_logo else best_logo

                    id_part = f' tvg-id="{cur_id}"' if cur_id else ""
                    logo_part = f' tvg-logo="{cur_logo}"' if cur_logo else ""

                    clean_info = f'#EXTINF:-1{id_part}{logo_part} group-title="{group_name}",{final_name}'
                    out.write(f"{clean_info}\n#EXTGRP:{group_name}\n{url}\n")

    total_streams = sum(len(v) for v in valid_by_idx.values())
    missing = []
    for idx, (w, grp) in enumerate(wanted_list):
        if idx not in valid_by_idx:
            missing.append(f"  - {w} (группа: {grp})")

    print("\n" + "=" * 40)
    print("📊 ОТЧЁТ О СБОРКЕ ПЛЕЙЛИСТА")
    print("=" * 40)
    print(f"✅ Найдено каналов: {len(valid_by_idx)} из {len(wanted_list)}")
    print(f"📺 Всего рабочих потоков: {total_streams}")
    if missing:
        print(f"❌ Не найдено каналов ({len(missing)}):")
        for m in missing:
            print(m)
    else:
        print("🎉 Отлично! Найдены абсолютно все каналы из списка!")
    print("=" * 40 + "\n")

if __name__ == "__main__":
    main()







