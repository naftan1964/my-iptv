import os
import re
import time
import gzip
import urllib.request
import concurrent.futures
import xml.etree.ElementTree as ET
from urllib.parse import urljoin


USER_AGENT = "Mozilla/5.0"
TIMEOUT = 10
MAX_STREAMS_PER_CHANNEL = 3

ALIASES = {
    "megogo спорт": ["megogo sport"],
    "окко прайм спорт": ["okko prime sport", "окко спорт прайм", "okko sport prime"],
    "окко спорт": ["okko sport"],
    "box sportcast": ["sportcast", "box sportcast 1", "бокс спорткаст"],
    "9-1-1": ["911", "911 служба спасения", "служба спасения", "служба спасения 911"],
    "history 2": ["viju history 2", "viju+ history 2", "viasat history 2", "h2", "хистори 2"],
    "viju tv1000": ["viju tv 1000", "tv1000", "tv 1000"],
    "viju tv1000 action": ["viju tv 1000 action", "tv1000 action", "tv 1000 action"],
    "viju tv1000 русское": ["viju tv 1000 русское", "tv1000 русское", "tv 1000 русское"],
    "cineman air crash": ["cineman aircrash", "cineman авиакатастрофы"],
    "z!russian": ["z русское", "z кинозал русское", "кинозал z russian"],
    "z!comedy": ["z комедия", "z кинозал комедия", "кинозал z comedy"],
    "z!thriller": ["z триллер", "z кинозал триллер", "кинозал z thriller"],
    "z!action": ["z боевик", "z кинозал боевик", "кинозал z action"],
    "z!adventure": ["z приключения", "z кинозал приключения", "кинозал z adventure"],
    "z!detective": ["z детектив", "z кинозал детектив", "кинозал z detective"],
    "z!fantastic": ["z фантастика", "z кинозал фантастика", "кинозал z fantastic"],
    "z!science": ["z наука", "z кинозал наука", "кинозал z science"],
    "z!travel": ["z путешествия", "z кинозал путешествия", "кинозал z travel"],
}


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


def is_unsupported_format(name, allow_all_formats=False):
    if allow_all_formats:
        return False
    return bool(re.search(r'\b(full\s*hd|fhd|orig|ориг|ариг|4k|uhd|ultra\s*hd|50\s*fps|60\s*fps|hdr)\b', name.lower()))


def clean_title(name, allow_all_formats=False):
    name = name.lower().strip()
    name = re.sub(r'[\(\[].*?[\)\]]', ' ', name)
    if allow_all_formats:
        name = re.sub(r'\b(full\s*hd|fhd|orig|ориг|ариг|4k|uhd|ultra\s*hd|50\s*fps|hd|sd|1080p?|720p?|russia|rus|ru|by|ua)\b', ' ', name)
    else:
        name = re.sub(r'\b(hd|sd|1080p?|720p?|60\s*fps|hdr|russia|rus|ru|by|ua)\b', ' ', name)
    name = re.sub(r'[^\w\sа-яёa-z0-9]', ' ', name)
    name = re.sub(r'\btv\b', 'тв', name)
    return re.sub(r'\s+', ' ', name).strip()


def is_channel_match(wanted, channel_name, allow_all_formats=False):
    c_clean = clean_title(channel_name, allow_all_formats)
    if not c_clean:
        return False
    for variant in [wanted] + ALIASES.get(wanted, []):
        w_clean = clean_title(variant, allow_all_formats)
        if w_clean and (w_clean == c_clean or c_clean == f"{w_clean} тв"):
            return True
    return False


def read_pinned_streams(filename, wanted_list):
    pinned = {}
    if not os.path.exists(filename):
        return pinned
    with open(filename, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#EXTM3U")]
    current_info = ""
    for line in lines:
        if line.startswith("#EXTINF"):
            current_info = line
        elif line.startswith(("http://", "https://")) and current_info:
            raw_name = current_info.rsplit(",", 1).pop().strip().lstrip("★").strip()
            name_lower = raw_name.lower()
            for idx, (w, grp) in enumerate(wanted_list):
                if is_channel_match(w, name_lower, allow_all_formats=True):
                    pinned.setdefault(idx, []).append((grp, current_info, line))
                    break
            current_info = ""
    return pinned


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
            xml_data = gzip.decompress(resp.read())
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


def stream_sort_key(item):
    grp, info, url, ping = item
    title = info.rsplit(",", 1).pop().strip()
    return (get_quality_score(title), ping)


def is_valid_ts_data(data):
    if not data or len(data) < 1024:
        return False
    low = data[:200].lstrip().lower()
    if low.startswith((b"<!doctype", b"<html", b"404", b"403", b"error", b"no signal")):
        return False
    # Проверка синхробайта MPEG-TS (0x47 каждые 188 байт)
    if len(data) >= 377 and data[0] == 0x47 and data[188] == 0x47 and data[376] == 0x47:
        return True
    return len(data) >= 4096


def fetch_bytes(url, max_bytes=4096, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            return b""
        return resp.read(max_bytes)


def check_stream(item):
    idx, key, group, info, url = item
    start = time.time()
    try:
        data = fetch_bytes(url, max_bytes=8192)
        if not data:
            return None

        # Если это HLS-плейлист (.m3u8) — проверяем реальный видеосегмент внутри
        if data.lstrip().startswith(b"#EXTM3U"):
            current_url = url
            text = data.decode("utf-8", errors="ignore")

            # Если это мастер-плейлист со вложенным .m3u8 — переходим глубже (до 2 уровней)
            for _ in range(2):
                sub_links = [
                    line.strip() for line in text.splitlines()
                    if line.strip() and not line.strip().startswith("#")
                ]
                if not sub_links:
                    return None
                next_url = urljoin(current_url, sub_links[0])
                if ".m3u8" in next_url.lower() and next_url != current_url:
                    sub_data = fetch_bytes(next_url, max_bytes=8192)
                    if not sub_data or not sub_data.lstrip().startswith(b"#EXTM3U"):
                        return None
                    current_url = next_url
                    text = sub_data.decode("utf-8", errors="ignore")
                else:
                    # Проверяем сам видеосегмент (.ts / .aac / .mp4)
                    seg_data = fetch_bytes(next_url, max_bytes=4096)
                    if is_valid_ts_data(seg_data):
                        return (idx, key, group, info, url, time.time() - start)
                    return None
            return None

        # Если это прямой поток (как 195.64.140.147:10103/99103)
        if is_valid_ts_data(data):
            return (idx, key, group, info, url, time.time() - start)
    except Exception:
        pass
    return None



def main():
    epg_host = "raw.githubusercontent.com"
    epg_path = "Lorax121/epg_v2/main/data/epg2.xml.gz"
    epg_url = f"https://{epg_host}/{epg_path}"

    epg_map = load_epg_data(epg_url)
    sources = read_sources("sources.txt")
    wanted_list = read_grouped_channels("channels.txt")
    pinned_by_idx = read_pinned_streams("pinned.m3u", wanted_list)

    candidates = []
    blocked_by_format = {}

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
                elif line.startswith(("http://", "https://")) and current_info:
                    raw_name = current_info.rsplit(",", 1).pop().strip()
                    if not has_timezone_shift(raw_name):
                        name_lower = raw_name.lower()
                        for idx, (w, grp) in enumerate(wanted_list):
                            is_kz = (grp.lower() == "кинозалы")
                            allow_all = is_kz or w.startswith(("z!", "cineman")) or w == "history 2"
                            if is_channel_match(w, name_lower, allow_all):
                                if not is_unsupported_format(raw_name, allow_all):
                                    candidates.append((idx, w, grp, current_info, line))
                                else:
                                    blocked_by_format.setdefault(idx, set()).add(raw_name)
                                break
                    current_info = ""
        except Exception as e:
            print(f"Ошибка загрузки {src}: {e}")

    valid_by_idx = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        for res in executor.map(check_stream, candidates):
            if res:
                idx, key, grp, info, url, ping = res
                valid_by_idx.setdefault(idx, []).append((grp, info, url, ping))

        for idx in valid_by_idx:
        valid_by_idx[idx].sort(key=stream_sort_key)
        unique_streams = []
        seen_urls = set()
        for s in valid_by_idx[idx]:
            if s[2] not in seen_urls:
                seen_urls.add(s[2])
                unique_streams.append(s)
        valid_by_idx[idx] = unique_streams[:MAX_STREAMS_PER_CHANNEL]

    seen_names = {}
    total_streams = 0
    found_indices = set()

    with open("my_playlist.m3u", "w", encoding="utf-8") as out:
        out.write(f'#EXTM3U url-tvg="{epg_url}"\n')
        for idx, (w, grp) in enumerate(wanted_list):
            p_streams = pinned_by_idx.get(idx, [])
            a_streams = valid_by_idx.get(idx, [])
            if not p_streams and not a_streams:
                continue

            found_indices.add(idx)
            pinned_urls = {u for _, _, u in p_streams}
            combined = [(g, i, u, True) for g, i, u in p_streams]
            for g, i, u, _ in a_streams:
                if u not in pinned_urls:
                    combined.append((g, i, u, False))

            best_id = ""
            best_logo = ""
            for _, s_info, _, _ in combined:
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
                for _, s_info, _, _ in combined:
                    s_raw = s_info.rsplit(",", 1).pop().strip().lstrip("★").strip()
                    s_id, s_logo = epg_map.get(normalize_name(s_raw), ("", ""))
                    if not epg_id and s_id:
                        epg_id = s_id
                    if not epg_logo and s_logo:
                        epg_logo = s_logo

            final_id = epg_id or best_id
            final_logo = epg_logo or best_logo

            for group_name, info, url, is_pinned in combined:
                total_streams += 1
                raw_name = info.rsplit(",", 1).pop().strip().lstrip("★").strip()
                base_name = f"★ {raw_name}" if is_pinned else raw_name
                name_key = base_name.lower()
                seen_names[name_key] = seen_names.get(name_key, 0) + 1
                final_name = f"{base_name} ({seen_names[name_key]})" if seen_names[name_key] > 1 else base_name

                catchup_tags = ""
                for tag in ("catchup", "catchup-days", "catchup-source", "timeshift"):
                    m_tag = re.search(rf'{tag}="([^"]+)"', info)
                    if m_tag:
                        catchup_tags += f' {tag}="{m_tag.group(1)}"'

                id_part = f' tvg-id="{final_id}"' if final_id else ""
                logo_part = f' tvg-logo="{final_logo}"' if final_logo else ""
                clean_info = f'#EXTINF:-1{id_part}{logo_part}{catchup_tags} group-title="{group_name}",{final_name}'
                out.write(f"{clean_info}\n#EXTGRP:{group_name}\n{url}\n")

    missing = []
    for idx, (w, grp) in enumerate(wanted_list):
        if idx not in found_indices:
            reason = ""
            if idx in blocked_by_format:
                reason = f" — отсеян фильтром формата ({', '.join(list(blocked_by_format[idx])[:2])})"
            missing.append(f"  - {w} (группа: {grp}){reason}")

    print("\n" + "=" * 40)
    print("📊 ОТЧЁТ О СБОРКЕ ПЛЕЙЛИСТА")
    print("=" * 40)
    print(f"✅ Найдено каналов: {len(found_indices)} из {len(wanted_list)}")
    print(f"📌 Закреплено потоков: {sum(len(v) for v in pinned_by_idx.values())}")
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








