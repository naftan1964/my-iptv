import urllib.request
import concurrent.futures
import gzip
import xml.etree.ElementTree as ET
import re
import time

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
                current_group = line.strip("[]").strip()
            else:
                channels.append((line.lower(), current_group))
    return channels


def has_timezone_shift(name):
    return bool(re.search(r'[\s\(\[][\+\-]\d{1,2}[\)\]]?\b', name))


def is_unsupported_format(name, is_kinozal=False):
    # Для кинозалов снимаем все ограничения по форматам и FPS
    if is_kinozal:
        return False
    name_low = name.lower()
    return bool(re.search(r'\b(full\s*hd|fhd|orig|ориг|ариг|4k|uhd|ultra\s*hd|50\s*fps|60\s*fps|hdr)\b', name_low))


def clean_title(name, is_kinozal=False):
    name = name.lower().strip()
    name = re.sub(r'[\(\[].*?[\)\]]', ' ', name)
    if is_kinozal:
        # В кинозалах убираем метки разрешения/orig/50fps для точного совпадения, но оставляем 60fps и hdr
        name = re.sub(r'\b(full\s*hd|fhd|orig|ориг|ариг|4k|uhd|ultra\s*hd|50\s*fps|hd|sd|1080p?|720p?|russia|russian|rus|ru|by|ua)\b', ' ', name)
    else:
        name = re.sub(r'\b(hd|sd|1080p?|720p?|60\s*fps|hdr|russia|russian|rus|ru|by|ua)\b', ' ', name)
    name = re.sub(r'[^\w\sа-яёa-z0-9]', ' ', name)
    name = re.sub(r'\btv\b', 'тв', name)
    return re.sub(r'\s+', ' ', name).strip()


def is_channel_match(wanted, channel_name, is_kinozal=False):
    w_clean = clean_title(wanted, is_kinozal)
    c_clean = clean_title(channel_name, is_kinozal)

    if not w_clean or not c_clean:
        return False

    # Строгое равенство очищенных названий (защита коротких имён)
    if w_clean == c_clean:
        return True

    # Допускаем только добавление "тв" в конце (например, "Комедия" == "Комедия ТВ")
    if c_clean == f"{w_clean} тв":
        return True

    return False


def normalize_name(name):
    name = name.lower().strip()
    name = re.sub(r'[\(\[].*?[\)\]]', '', name)
    name = re.sub(r'[!\.\-\+:]', ' ', name)
    name = re.sub(r'\b(full\s*hd|fhd|hd|sd|4k|uhd|1080p?|720p?|50\s*fps|60\s*fps|hdr|orig|russia|rus|тв|tv)\b', '', name)
    return re.sub(r'\s+', ' ', name).strip()


def load_epg_data(epg_url):
    epg_map = {}
    try:
        req = urllib.request.Request(epg_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw_data = resp.read()
        xml_data = gzip.decompress(raw_data)
        root = ET.fromstring(xml_data)
        for ch in root.findall("channel"):
            ch_id = (ch.get("id") or "").strip()
            icon_el = ch.find("icon")
            icon_src = (icon_el.get("src") or "").strip() if icon_el is not None else ""
            for dn in ch.findall("display-name"):
                if dn.text:
                    key = normalize_name(dn.text)
                    if key:
                        old_id, old_logo = epg_map.get(key, ("", ""))
                        if not old_logo and icon_src:
                            epg_map[key] = (ch_id or old_id, icon_src)
                        elif key not in epg_map:
                            epg_map[key] = (ch_id, icon_src)
        print(f"База EPG загружена: {len(epg_map)} каналов с иконками.")
    except Exception as e:
        print(f"Не удалось загрузить иконки из EPG: {e}")
    return epg_map


def get_quality_score(name):
    name_low = name.lower()
    if re.search(r'\b(1080p?|720p?|hd)\b', name_low):
        return 1
    if re.search(r'\bsd\b', name_low):
        return 3
    return 2


def check_stream(item):
    idx, key, group, info, url = item
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status == 200:
                chunk = resp.read(512)
                if chunk and not chunk.lstrip().lower().startswith((b"<!doctype", b"<html")):
                    ping = time.time() - start
                    return (idx, key, group, info, url, ping)
    except Exception:
        pass
    return None


def main():
    epg_url = "https://raw.githubusercontent.com/Lorax121/epg_v2/main/data/epg2.xml.gz"
    epg_map = load_epg_data(epg_url)

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
                    raw_name = current_info.rsplit(",", 1).pop().strip()
                    if not has_timezone_shift(raw_name):
                        name_lower = raw_name.lower()
                        for idx, (w, grp) in enumerate(wanted_list):
                            is_kz = (grp.lower() == "кинозалы")
                            if not is_unsupported_format(raw_name, is_kz):
                                if is_channel_match(w, name_lower, is_kz):
                                    candidates.append((idx, w, grp, current_info, line))
                                    break
                    current_info = ""
        except Exception as e:
            print(f"Ошибка загрузки {src}: {e}")

    valid_by_idx = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        for res in executor.map(check_stream, candidates):
            if res:
                idx, key, grp, info, url, ping = res
                bucket = valid_by_idx.setdefault(idx, [])
                bucket.append((grp, info, url, ping))

    def stream_sort_key(item):
        grp, info, url, ping = item
        ch_title = info.rsplit(",", 1).pop().strip()
        return (get_quality_score(ch_title), ping)

    for idx in valid_by_idx:
        valid_by_idx[idx].sort(key=stream_sort_key)
        valid_by_idx[idx] = valid_by_idx[idx][:MAX_STREAMS_PER_CHANNEL]

    seen_names = {}

    with open("my_playlist.m3u", "w", encoding="utf-8") as out:
        out.write(f'#EXTM3U url-tvg="{epg_url}"\n')
        for idx, (w, grp) in enumerate(wanted_list):
            if idx in valid_by_idx:
                streams = valid_by_idx[idx]
                best_id = ""
                best_logo = ""
                for _, s_info, _, _ in streams:
                    if not best_id:
                        m_id = re.search(r'tvg-id="([^"]+)"', s_info)
                        if m_id:
                            best_id = m_id.group(1)
                    if not best_logo:
                        m_logo = re.search(r'tvg-logo="([^"]+)"', s_info)
                        if m_logo:
                            best_logo = m_logo.group(1)

                epg_id, epg_logo = epg_map.get(normalize_name(w), ("", ""))
                if not epg_id or not epg_logo:
                    for _, s_info, _, _ in streams:
                        s_raw = s_info.rsplit(",", 1).pop().strip()
                        s_id, s_logo = epg_map.get(normalize_name(s_raw), ("", ""))
                        if not epg_id and s_id:
                            epg_id = s_id
                        if not epg_logo and s_logo:
                            epg_logo = s_logo

                final_id = epg_id or best_id
                final_logo = epg_logo or best_logo

                for group_name, info, url, _ in streams:
                    raw_name = info.rsplit(",", 1).pop().strip()
                    name_key = raw_name.lower()
                    seen_names[name_key] = seen_names.get(name_key, 0) + 1
                    final_name = f"{raw_name} ({seen_names[name_key]})" if seen_names[name_key] > 1 else raw_name

                    catchup_tags = ""
                    for tag in ("catchup", "catchup-days", "catchup-source", "timeshift"):
                        m_tag = re.search(rf'{tag}="([^"]+)"', info)
                        if m_tag:
                            catchup_tags += f' {tag}="{m_tag.group(1)}"'

                    id_part = f' tvg-id="{final_id}"' if final_id else ""
                    logo_part = f' tvg-logo="{final_logo}"' if final_logo else ""

                    clean_info = f'#EXTINF:-1{id_part}{logo_part}{catchup_tags} group-title="{group_name}",{final_name}'
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








