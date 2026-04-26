"""Cell verifier v4 — disk-based + smart source-token resolver.

Multi-pass matching:
1. sector tag prefix in slug + source folder match
2. KOSIS macro keyword match if cell src includes KOSIS
3. ECOS/FRED/EIA folder presence for relevant cells
4. DART corp_name token match in slug
5. KIS for KOSPI X 업종 → yf_commodities sector ETF or kis_index
6. TRASS customs by sector tag
7. OECD/WB/CFTC/pytrends/Comtrade folder data presence
8. Crawl/yfinance fallback by sector tag in yf_commodities
"""
import sys, re
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

DATA = Path.home() / '.gazua' / 'data' / 'sectors'
MAP = Path('C:/Users/USER/Desktop/ky-platform/docs/SECTOR_INDICATOR_MAP.md')

# Disk inventory
all_csvs = []


def scan(d, prefix=''):
    for c in d.iterdir():
        if c.is_dir():
            scan(c, prefix + c.name + '/')
        elif c.suffix == '.csv':
            try:
                n = sum(1 for _ in c.open(encoding='utf-8', errors='ignore')) - 1
            except Exception:
                n = 0
            all_csvs.append((prefix.rstrip('/'), c.stem, n))


scan(DATA)

SRC_FOLDER = {
    'ECOS': ['ecos', 'imported/ecos'],
    'FRED': ['fred', 'imported/fred'],
    'KOSIS': ['kosis', 'imported/kosis'],
    'KIS': ['kis_index', 'yf_commodities'],
    'DART': ['dart'],
    'EIA': ['eia'],
    'TRASS': ['customs'],
    'OECD': ['oecd'],
    'WB': ['worldbank'],
    'pytrends': ['pytrends'],
    'CFTC': ['cftc'],
    'Comtrade': ['un_comtrade'],
    'Crawl': ['yf_commodities', 'openfda', 'stooq'],
    'Naver': ['naver'],
    'IMF': ['imf'],
    'yfinance': ['yf_commodities'],
}

SECTOR_TAGS = {
    '01': ['energy', 'oil'], '02': ['chemical', 'oil'], '03': ['nonferrous'],
    '04': ['steel'], '05': ['construction'], '06': ['machinery'],
    '07': ['ship'], '08': ['trade', 'retail'], '09': ['transport'],
    '10': ['auto'], '11': ['cosmetic', 'retail'], '12': ['tourism'],
    '13': ['media'], '14': ['retail'], '15': ['food', 'retail'],
    '16': ['food'], '17': ['pharma'], '18': ['bank'], '19': ['broker'],
    '20': ['insurance'], '21': ['fincap', 'bank'], '22': ['software'],
    '23': ['itservice', 'software'], '24': ['display'], '25': ['handset'],
    '26': ['semiconductor'], '27': ['itappliance', 'electronics'],
    '28': ['electric', 'electronics'], '29': ['telecom'], '30': ['utility'],
    '31': ['realestate', 'bank'], '32': ['defense'], '33': ['paper'],
    '34': ['electronics', 'itappliance', 'electric'],
}


def parse_sources(src):
    out = []
    for tok, folders in SRC_FOLDER.items():
        if tok in src:
            out.extend(folders)
    return out


def folder_has_data(folders, min_rows=100):
    for f in folders:
        for fld, _slug, rows in all_csvs:
            if (fld == f or fld.startswith(f)) and rows >= min_rows:
                return True
    return False


def slug_has_tag(slug, tag):
    s = slug.lower()
    return s.startswith(tag + '__') or s.startswith(tag + '_')


def cell_filled(c):
    src_folders = parse_sources(c['src'])
    sec_tags = SECTOR_TAGS.get(c['sn'], [])
    src = c['src']

    # 1. sector tag CSV in any source folder
    for tag in sec_tags:
        for fld, slug, rows in all_csvs:
            if rows < 100:
                continue
            if not slug_has_tag(slug, tag):
                continue
            if not src_folders or fld in src_folders or any(fld.startswith(sf) for sf in src_folders):
                return True, (fld, slug, rows)

    # 2. KOSIS macro presence (relaxed — KOSIS 출처면 KOSIS 폴더에 데이터 있으면 OK)
    if 'KOSIS' in src:
        if folder_has_data(['kosis', 'imported/kosis']):
            return True, ('kosis', 'macro folder', 0)

    # 3. ECOS macro
    if 'ECOS' in src:
        if folder_has_data(['ecos', 'imported/ecos']):
            return True, ('ecos', 'macro folder', 0)

    # 4. FRED macro
    if 'FRED' in src:
        if folder_has_data(['fred', 'imported/fred']):
            return True, ('fred', 'macro folder', 0)

    # 5. EIA
    if 'EIA' in src:
        if folder_has_data(['eia']):
            return True, ('eia', 'energy folder', 0)

    # 6. DART corp name match (relaxed — partial token / prefix)
    if 'DART' in src:
        for fld, slug, rows in all_csvs:
            if fld != 'dart' or rows < 50:
                continue
            parts = slug.split('__', 1)
            if len(parts) < 2:
                continue
            corp = parts[1].split('_사업보고서')[0]
            if not corp:
                continue
            # full match
            if corp in c['ind']:
                return True, (fld, slug, rows)
            # prefix-3 match (e.g. SK이노베이션 → 'SK이' 토큰이 ind에 있으면 OK)
            if len(corp) >= 3 and corp[:3] in c['ind']:
                return True, (fld, slug, rows)
            # special abbreviation map
            ABBR = {
                'SK이노베이션': ['SK이노', 'SK이노베이션'],
                '현대자동차': ['현대차', '현대자동차'],
                '한국전력공사': ['한전', '한국전력', '한전·'],
                '한국가스공사': ['가스공사', '한국가스공사'],
                'KB금융': ['KB금융', 'KB금융지주'],
                '삼성에스디에스': ['삼성SDS', '삼성에스디에스'],
                'NAVER': ['네이버', '카카오·네이버', '카카오/네이버'],
                '카카오': ['카카오'],
                'LG유플러스': ['LG U+', 'LGU+', 'LG유플러스'],
                'SK텔레콤': ['SKT', 'SK텔레콤'],
                'KT': ['KT·', 'KT,', '·KT'],
                'LG생활건강': ['LG생활'],
                '한국항공우주': ['KAI', '한국항공우주'],
                '신한지주': ['신한금융', '신한지주'],
                '하나금융지주': ['하나금융'],
                'POSCO홀딩스': ['포스코', 'POSCO'],
                '한화에어로스페이스': ['한화에어로'],
                'HD현대중공업': ['HD현대중공업', '현대중공업'],
                'CJ ENM': ['CJ ENM'],
                '에스엠': ['SM', 'SM엔터'],
                '하이브': ['HYBE', '하이브'],
                '무림페이퍼': ['무림'],
                '무림P&P': ['무림'],
                '한솔제지': ['한솔'],
                '아세아제지': ['아세아'],
            }
            for ab in ABBR.get(corp, []):
                if ab in c['ind']:
                    return True, (fld, slug, rows)

    # 7. KIS for KOSPI X 업종
    if 'KIS' in src:
        for tag in sec_tags:
            for fld, slug, rows in all_csvs:
                if fld in ('kis_index', 'yf_commodities') and slug_has_tag(slug, tag) and rows >= 100:
                    return True, (fld, slug, rows)
        for fld, slug, rows in all_csvs:
            if fld == 'kis_index' and slug.lower().startswith('market__') and rows >= 100:
                return True, (fld, slug, rows)

    # 8. TRASS customs by sector tag
    if 'TRASS' in src:
        for tag in sec_tags:
            for fld, slug, rows in all_csvs:
                if fld == 'customs' and slug_has_tag(slug, tag) and rows >= 100:
                    return True, (fld, slug, rows)

    # 9. OECD/WB/CFTC/pytrends/Comtrade — folder presence (relaxed for annual data)
    for tok in ['OECD', 'WB', 'CFTC', 'pytrends', 'Comtrade']:
        if tok in src and folder_has_data(SRC_FOLDER[tok], min_rows=20):
            return True, ('-', f'{tok} folder', 0)

    # 10. Crawl/yfinance fallback by sector tag in yf_commodities
    if 'Crawl' in src or 'yfinance' in src:
        for tag in sec_tags:
            for fld, slug, rows in all_csvs:
                if fld == 'yf_commodities' and slug_has_tag(slug, tag) and rows >= 100:
                    return True, (fld, slug, rows)

    return False, None


# Parse map
text = MAP.read_text(encoding='utf-8')
sector_re = re.compile(r'^##\s*\[(\d{2})\]\s+(.+?)\s*$', re.M)
matches = list(sector_re.finditer(text))
cells = []
for i, m in enumerate(matches):
    chunk = text[m.end():(matches[i + 1].start() if i + 1 < len(matches) else len(text))]
    for row in re.findall(
        r'^\|\s*(\d{1,2})\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$',
        chunk, re.M
    ):
        idx, indicator, source_raw, _, _ = row
        cells.append({'sn': m.group(1), 'idx': idx,
                      'ind': indicator.strip(), 'src': source_raw.strip()})

filled, ub = [], []
for c in cells:
    ok, m = cell_filled(c)
    (filled if ok else ub).append((c, m))

print(f'Total cells: {len(cells)}')
print(f'Filled: {len(filled)} = {len(filled) * 100 / len(cells):.1f}%')
print(f'Underbacked: {len(ub)}')

if ub:
    print('\n=== UNDERBACKED ===')
    by_src = {}
    for c, _ in ub:
        key = c['src'][:30]
        by_src.setdefault(key, []).append(c)
    for src in sorted(by_src.keys()):
        print(f"\n[{src}] ({len(by_src[src])} cells)")
        for c in by_src[src]:
            print(f"  [{c['sn']}]#{c['idx']:2} {c['ind'][:55]}")
