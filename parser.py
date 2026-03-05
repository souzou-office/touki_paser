"""登記情報PDFパーサー

登記情報提供サービスから取得したPDFを解析し、構造化データに変換する。
対応種別: 土地全部事項、建物全部事項、区分建物全部事項
"""

import re
import sys
import io
import json
from typing import Optional
import pdfplumber


# ── ユーティリティ ──

ZEN_NUM = str.maketrans("０１２３４５６７８９", "0123456789")


def zen_to_han_num(s: str) -> str:
    return s.translate(ZEN_NUM)


def normalize(s: str) -> str:
    s = re.sub(r"[\s　]+", " ", s).strip()
    return s


def clean_cell(s: str) -> str:
    if not s:
        return ""
    # 罫線文字を除去
    s = re.sub(r"[┏┓┗┛┠┨┯┷┃│─━┬┴┼┤├╂╋╃╄╅╆╇╈╉╊]+", "", s)
    # Private Use Area文字（余白マーカー等）を除去
    s = re.sub(r"[\ue000-\uf8ff]+", "", s)
    return normalize(s)


def clean_menseki(s: str) -> str:
    """面積フィールドをクリーンアップ。 '：' → '.' 変換し、空値を除外"""
    if not s:
        return ""
    val = clean_cell(s).replace("：", ".").strip()
    # "." だけ or 空 → 空
    if val in ("", ".", " ."):
        return ""
    # 末尾の不要なドットを除去（整数部のみの場合 "１４６." → "１４６"）
    val = re.sub(r"\.$", "", val)
    return val


# ── セクション分割 ──

SECTION_PATTERNS = {
    "表題部_土地": re.compile(r"表\s*題\s*部\s*（\s*土地の表示\s*）"),
    "表題部_建物": re.compile(r"表\s*題\s*部\s*（\s*主である建物の表示\s*）"),
    "表題部_一棟": re.compile(r"表\s*題\s*部\s*（\s*一棟の建物の表示\s*）"),
    "表題部_敷地権目的": re.compile(r"表\s*題\s*部\s*（\s*敷地権の目的である土地の表示\s*）"),
    "表題部_専有": re.compile(r"表\s*題\s*部\s*（\s*専有部分の建物の表示\s*）"),
    "表題部_敷地権": re.compile(r"表\s*題\s*部\s*（\s*敷地権の表示\s*）"),
    "甲区": re.compile(r"権\s*利\s*部\s*（\s*甲\s*区\s*）"),
    "乙区": re.compile(r"権\s*利\s*部\s*（\s*乙\s*区\s*）"),
    "共同担保目録": re.compile(r"共\s*同\s*担\s*保\s*目\s*録"),
}


def extract_text_from_pdf(pdf_path: str) -> str:
    pdf = pdfplumber.open(pdf_path)
    pages_text = []
    for page in pdf.pages:
        text = page.extract_text()
        if text:
            pages_text.append(text)
    pdf.close()
    return "\n".join(pages_text)


def split_sections(text: str) -> list[tuple[str, list[str]]]:
    lines = text.split("\n")
    sections = []
    current_name = "header"
    current_lines = []

    for line in lines:
        matched = None
        for name, pattern in SECTION_PATTERNS.items():
            if pattern.search(line):
                matched = name
                break

        if matched:
            if current_lines:
                sections.append((current_name, current_lines))
            current_name = matched
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_name, current_lines))

    return sections


# ── 行ユーティリティ ──

def split_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("┃"):
        stripped = stripped[1:]
    if stripped.endswith("┃"):
        stripped = stripped[:-1]
    parts = re.split(r"[│┃]", stripped)
    return [p.strip() for p in parts]


def is_data_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("┃") or stripped.startswith("│")


def is_header_row(cols: list[str], keywords: list[str]) -> bool:
    """カラムヘッダー行かどうかを判定"""
    text = " ".join(cols)
    return any(k in text for k in keywords)


# ── ヘッダーパーサー ──

def parse_header(lines: list[str]) -> dict:
    result = {}
    all_kayabanago_lines = []

    for line in lines:
        line_n = normalize(line)

        m = re.search(r"(\d{4}[／/]\d{2}[／/]\d{2})\s+(\d{2}[：:]\d{2})\s*現在の情報", zen_to_han_num(line_n))
        if m:
            result["現在日時"] = f"{m.group(1)} {m.group(2)}"

        m = re.search(r"発行年月日[：:](.+)", line_n)
        if m:
            result["発行年月日"] = normalize(m.group(1))

        m = re.search(r"照会番号\s*[：:](.+)", line_n)
        if m:
            result["照会番号"] = normalize(m.group(1))

        if "専有部分の家屋番号" in line_n:
            cols = split_row(line)
            if len(cols) >= 2:
                all_kayabanago_lines.append(normalize(cols[1]))

    if all_kayabanago_lines:
        result["専有部分の家屋番号一覧"] = " ".join(all_kayabanago_lines)

    return result


# ── 表題部パーサー（土地） ──

def parse_hyodaibu_tochi(lines: list[str]) -> dict:
    result = {
        "種別": "土地",
        "調製": "",
        "不動産番号": "",
        "地図番号": "",
        "筆界特定": "",
        "所在": [],
        "地番履歴": [],
    }

    for line in lines:
        line_n = normalize(line)

        # 調製・不動産番号
        m = re.search(r"調製[│┃]\s*(.+?)\s*[│┃]?\s*不動産番号[│┃]\s*(.+?)\s*┃", line_n)
        if m:
            result["調製"] = clean_cell(m.group(1))
            result["不動産番号"] = clean_cell(m.group(2))
            continue

        if not is_data_line(line):
            continue

        cols = split_row(line)
        joined = " ".join(cols)

        # 地図番号・筆界特定
        if "地図番号" in joined:
            for i, c in enumerate(cols):
                if "地図番号" in c and i + 1 < len(cols):
                    result["地図番号"] = clean_cell(cols[i + 1])
                if "筆界特定" in c and i + 1 < len(cols):
                    result["筆界特定"] = clean_cell(cols[i + 1])
            continue

        # ヘッダー行スキップ
        if is_header_row(cols, ["① 地 番", "①地", "地 番", "②地 目", "③ 地"]):
            if "①" in joined:
                continue

        # 所在
        if len(cols) >= 2:
            c0 = clean_cell(cols[0])
            if "所" in c0 and "在" in c0 and "所在図" not in c0:
                addr = clean_cell(cols[1])
                if addr:
                    result["所在"].append(addr)
                continue

        # 所在の続き行（行政区画変更等）
        if len(cols) >= 2:
            c0 = clean_cell(cols[0])
            # 4カラムの場合は地番データの可能性が高い → スキップして地番処理へ
            if len(cols) >= 4:
                pass  # 下の地番処理で扱う
            elif not c0:
                last_col = clean_cell(cols[-1])
                middle = clean_cell(cols[1]) if len(cols) > 2 else ""
                if middle and ("市" in middle or "町" in middle or "区" in middle or "郡" in middle):
                    if last_col and ("行政区画" in last_col or "登記" in last_col):
                        result["所在"].append(f"{middle}（{last_col}）")
                        continue

        # 地番・地目・地積データ
        if len(cols) >= 4:
            chiban = clean_cell(cols[0])
            chimoku = clean_cell(cols[1])
            chiseki_raw = clean_cell(cols[2])
            reason = clean_cell(cols[3])
            chiseki = clean_menseki(chiseki_raw)

            # ヘッダー判定
            if "①" in chiban or "地 番" in chiban or "地目" in chimoku and "②" in chimoku:
                continue

            has_data = chiban or chimoku or chiseki

            if has_data:
                if chiban and chiban not in ["余 白"]:
                    # 新規エントリ
                    entry = {
                        "地番": chiban,
                        "地目": chimoku,
                        "地積": chiseki,
                        "原因日付": reason,
                    }
                    result["地番履歴"].append(entry)
                elif result["地番履歴"]:
                    # 既存エントリへの追記（地番なし＝現在行の変更）
                    last = result["地番履歴"][-1]
                    if chimoku:
                        last["地目"] = chimoku
                    if chiseki:
                        last["地積"] = chiseki
                    if reason:
                        last.setdefault("追加情報", []).append(reason)
            elif reason and result["地番履歴"]:
                # データなし・原因のみ（移記情報等）
                last = result["地番履歴"][-1]
                last.setdefault("追加情報", []).append(reason)

    # 最終的な地番・地目・地積（最新の有効値を採用）
    if result["地番履歴"]:
        # 最新の地番を持つエントリを基準にする
        latest_chiban = ""
        latest_chimoku = ""
        latest_chiseki = ""
        for entry in result["地番履歴"]:
            if entry.get("地番") and entry["地番"] not in ["余 白"]:
                latest_chiban = entry["地番"]
            if entry.get("地目"):
                latest_chimoku = entry["地目"]
            if entry.get("地積"):
                latest_chiseki = entry["地積"]
        result["地番"] = latest_chiban
        result["地目"] = latest_chimoku
        result["地積"] = latest_chiseki

    return result


# ── 表題部パーサー（建物 - 主たる建物） ──

def parse_hyodaibu_tatemono(lines: list[str]) -> dict:
    result = {
        "種別": "建物",
        "調製": "",
        "不動産番号": "",
        "所在図番号": "",
        "所在": "",
        "家屋番号": "",
        "種類": "",
        "構造": "",
        "床面積": [],
        "原因日付": [],
    }

    header_seen = False

    for line in lines:
        line_n = normalize(line)

        m = re.search(r"調製[│┃]\s*(.+?)\s*[│┃]?\s*不動産番号[│┃]\s*(.+?)\s*┃", line_n)
        if m:
            result["調製"] = clean_cell(m.group(1))
            result["不動産番号"] = clean_cell(m.group(2))
            continue

        if not is_data_line(line):
            continue

        cols = split_row(line)
        joined = " ".join(cols)

        # 所在図番号
        if "所在図番号" in joined:
            for i, c in enumerate(cols):
                if "所在図番号" in c and i + 1 < len(cols):
                    result["所在図番号"] = clean_cell(cols[i + 1])
            continue

        # 所在
        if any("所" in clean_cell(c) and "在" in clean_cell(c) and "所在図" not in c for c in cols):
            if len(cols) >= 2:
                result["所在"] = clean_cell(cols[1])
            continue

        # 家屋番号
        if "家屋番号" in joined:
            for i, c in enumerate(cols):
                if "家屋番号" in c and i + 1 < len(cols):
                    result["家屋番号"] = clean_cell(cols[i + 1])
            continue

        # 建物の名称
        if "建物の名称" in joined:
            for i, c in enumerate(cols):
                if "建物の名称" in c and i + 1 < len(cols):
                    result["建物の名称"] = clean_cell(cols[i + 1])
            continue

        # ① 種類 / ② 構造 / ③ 床面積 ヘッダー行
        if "①" in joined and ("種" in joined or "構" in joined):
            header_seen = True
            continue

        # ヘッダー後のデータ行
        if header_seen and len(cols) >= 4:
            shurui = clean_cell(cols[0])
            kouzou = clean_cell(cols[1])
            menseki = clean_menseki(cols[2])
            reason = clean_cell(cols[3])

            if shurui and "余" not in shurui:
                result["種類"] = shurui
            if kouzou and "余" not in kouzou:
                if not result["構造"]:
                    result["構造"] = kouzou
                elif any(k in kouzou for k in ["造", "建", "階"]):
                    # 構造の続き行（「階建」等）
                    result["構造"] += kouzou
            if menseki:
                result["床面積"].append(menseki)
            if reason:
                result["原因日付"].append(reason)

        elif header_seen and len(cols) >= 3:
            for c in cols:
                c_clean = clean_cell(c)
                menseki = clean_menseki(c)
                if menseki and ("階" in c_clean or re.search(r"\d", c_clean)):
                    result["床面積"].append(menseki)
                elif c_clean and any(k in c_clean for k in ["新築", "増築", "変更", "移記", "取毀", "附則", "規定", "管轄"]):
                    result["原因日付"].append(c_clean)

    return result


# ── 表題部パーサー（一棟の建物） ──

def parse_hyodaibu_ittou(lines: list[str]) -> dict:
    result = {
        "調製": "",
        "所在図番号": "",
        "所在": "",
        "建物の名称": "",
        "構造": "",
        "床面積": [],
        "原因日付": [],
    }

    header_seen = False

    for line in lines:
        line_n = normalize(line)

        m = re.search(r"調製[│┃]\s*(.+?)\s*[│┃]?\s*所在図番号[│┃]\s*(.+?)\s*┃", line_n)
        if not m:
            m = re.search(r"調製[│┃]\s*(.+?)\s*[│┃]\s*所在図番号[│┃]\s*(.*)", line_n)
        if m:
            result["調製"] = clean_cell(m.group(1))
            result["所在図番号"] = clean_cell(m.group(2))
            continue

        if not is_data_line(line):
            continue

        cols = split_row(line)
        joined = " ".join(cols)

        if any("所" in clean_cell(c) and "在" in clean_cell(c) and "所在図" not in c and "所在及" not in c for c in cols):
            if len(cols) >= 2:
                result["所在"] = clean_cell(cols[1])
            continue

        if "建物の名称" in joined:
            for i, c in enumerate(cols):
                if "建物の名称" in c and i + 1 < len(cols):
                    result["建物の名称"] = clean_cell(cols[i + 1])
            continue

        if "①" in joined and ("構" in joined):
            header_seen = True
            continue

        if header_seen and len(cols) >= 3:
            kouzou = clean_cell(cols[0])
            menseki = clean_menseki(cols[1])
            reason = clean_cell(cols[2])

            if kouzou and any(k in kouzou for k in ["造", "建", "階"]):
                if not result["構造"]:
                    result["構造"] = kouzou
                else:
                    result["構造"] += kouzou
            if menseki and ("階" in cols[1] or re.search(r"\d", menseki)):
                result["床面積"].append(menseki)
            if reason:
                result["原因日付"].append(reason)

    return result


# ── 表題部パーサー（敷地権の目的である土地の表示） ──

def parse_shikichiken_tochi(lines: list[str]) -> list[dict]:
    entries = []
    current = None
    header_seen = False

    for line in lines:
        if not is_data_line(line):
            continue

        cols = split_row(line)
        joined = " ".join(cols)

        if "土地の符号" in joined or "所在及び地番" in joined or "所 在 及 び 地 番" in joined:
            header_seen = True
            continue

        if not header_seen:
            continue

        if len(cols) >= 5:
            fugou = clean_cell(cols[0])
            shozai = clean_cell(cols[1])
            chimoku = clean_cell(cols[2])
            chiseki = clean_menseki(cols[3])
            touroku = clean_cell(cols[4])

            if fugou:
                current = {
                    "土地の符号": fugou,
                    "所在及び地番": shozai,
                    "地目": chimoku,
                    "地積": chiseki,
                    "登記の日付": touroku,
                }
                entries.append(current)
            elif current:
                if shozai:
                    current["所在及び地番"] += shozai
                if touroku:
                    current["登記の日付"] += " " + touroku if current["登記の日付"] else touroku

    return entries


# ── 表題部パーサー（専有部分の建物の表示） ──

def parse_hyodaibu_senyuu(lines: list[str]) -> dict:
    result = {
        "不動産番号": "",
        "家屋番号": "",
        "建物の名称": "",
        "種類": "",
        "構造": "",
        "床面積": [],
        "原因日付": [],
    }

    header_seen = False

    for line in lines:
        line_n = normalize(line)

        m = re.search(r"不動産番号[│┃]\s*(.+?)\s*┃", line_n)
        if m:
            result["不動産番号"] = clean_cell(m.group(1))

        if not is_data_line(line):
            continue

        cols = split_row(line)
        joined = " ".join(cols)

        if "家屋番号" in joined:
            for i, c in enumerate(cols):
                if "家屋番号" in c and i + 1 < len(cols):
                    result["家屋番号"] = clean_cell(cols[i + 1])
            continue

        if "建物の名称" in joined:
            for i, c in enumerate(cols):
                if "建物の名称" in c and i + 1 < len(cols):
                    result["建物の名称"] = clean_cell(cols[i + 1])
            continue

        if "①" in joined and ("種" in joined):
            header_seen = True
            continue

        if header_seen and len(cols) >= 4:
            shurui = clean_cell(cols[0])
            kouzou = clean_cell(cols[1])
            menseki = clean_menseki(cols[2])
            reason = clean_cell(cols[3])

            if shurui and "余" not in shurui:
                result["種類"] = shurui
            if kouzou and "余" not in kouzou:
                if not result["構造"]:
                    result["構造"] = kouzou
                elif any(k in kouzou for k in ["造", "建", "階"]):
                    result["構造"] += kouzou
            if menseki:
                result["床面積"].append(menseki)
            if reason:
                result["原因日付"].append(reason)

    return result


# ── 表題部パーサー（敷地権の表示） ──

def parse_shikichiken_hyoji(lines: list[str]) -> list[dict]:
    entries = []
    current = None
    header_seen = False

    for line in lines:
        if not is_data_line(line):
            continue

        cols = split_row(line)
        joined = " ".join(cols)

        if "土地の符号" in joined and "敷地権の種類" in joined:
            header_seen = True
            continue

        if not header_seen:
            continue

        if len(cols) >= 4:
            fugou = clean_cell(cols[0])
            shurui = clean_cell(cols[1])
            wariai = clean_cell(cols[2])
            reason = clean_cell(cols[3])

            if fugou:
                current = {
                    "土地の符号": fugou,
                    "敷地権の種類": shurui,
                    "敷地権の割合": wariai,
                    "原因日付": reason,
                }
                entries.append(current)
            elif current and reason:
                current["原因日付"] += " " + reason

    return entries


# ── 権利部パーサー（甲区・乙区共通） ──

def parse_kenribu(lines: list[str], section_name: str) -> list[dict]:
    entries = []
    current = None

    for line in lines:
        if not is_data_line(line):
            continue

        cols = split_row(line)
        joined = " ".join(cols)

        # ヘッダースキップ
        if "権 利 部" in joined or "権利部" in joined:
            continue
        if "順位番号" in joined and ("登 記 の 目 的" in joined or "登記の目的" in joined):
            continue

        if len(cols) < 4:
            continue

        juniban = clean_cell(cols[0])
        mokuteki = clean_cell(cols[1])
        uketsuke = clean_cell(cols[2])
        details = clean_cell(cols[3])

        # 新しいエントリ
        if juniban and (re.match(r"^[０-９\d]+$", juniban) or re.match(r"^付記", juniban)):
            current = {
                "順位番号": juniban,
                "登記の目的": mokuteki,
                "受付年月日": "",
                "受付番号": "",
                "詳細": {},
            }
            if uketsuke:
                _parse_uketsuke(current, uketsuke)
            if details:
                _parse_detail_line(current["詳細"], details)
            entries.append(current)

        elif current:
            # 続き行
            if mokuteki:
                current["登記の目的"] += mokuteki
            if uketsuke:
                if not current["受付年月日"]:
                    _parse_uketsuke(current, uketsuke)
                else:
                    current["受付番号"] += clean_cell(uketsuke)
            if details:
                _parse_detail_line(current["詳細"], details)

        elif details:
            # 移記情報等（順位番号なし）
            orphan = {
                "順位番号": "",
                "登記の目的": mokuteki,
                "受付年月日": "",
                "受付番号": "",
                "詳細": {},
            }
            if uketsuke:
                _parse_uketsuke(orphan, uketsuke)
            _parse_detail_line(orphan["詳細"], details)
            entries.append(orphan)

    return entries


def _parse_uketsuke(entry: dict, uketsuke: str):
    """受付年月日・受付番号を分離"""
    parts = uketsuke.split("第")
    entry["受付年月日"] = clean_cell(parts[0]) if parts[0] else ""
    entry["受付番号"] = "第" + clean_cell(parts[1]) if len(parts) > 1 else ""


def _parse_detail_line(details: dict, text: str):
    """権利者その他の事項の1行をパース"""
    text = text.strip()
    if not text:
        return

    key_patterns = [
        ("原因", r"^原因\s+(.+)"),
        ("所有者", r"^所有者\s+(.+)"),
        ("権利者", r"^権利者\s+(.+)"),
        ("共有者", r"^共有者\s+(.+)"),
        ("債権額", r"^債権額\s+(.+)"),
        ("極度額", r"^極度額\s+(.+)"),
        ("利息", r"^利息\s+(.+)"),
        ("損害金", r"^損害金\s+(.+)"),
        ("債務者", r"^債務者\s+(.+)"),
        ("抵当権者", r"^抵当権者\s+(.+)"),
        ("根抵当権者", r"^根抵当権者\s+(.+)"),
        ("債権の範囲", r"^債権の範囲\s+(.+)"),
        ("共同担保", r"^共同担保\s+(.+)"),
        ("順位移記", r"^(順位.*番の登記を移記)"),
        ("住所", r"^住所\s+(.+)"),
        ("氏名住所", r"^氏名住所\s+(.+)"),
        ("管轄転属", r"^(管轄転属により登記.*)"),
    ]

    for key, pattern in key_patterns:
        m = re.match(pattern, text)
        if m:
            val = m.group(1).strip()
            if key in details:
                details[key] += " " + val
            else:
                details[key] = val
            return

    # マッチしない場合
    if details:
        last_key = list(details.keys())[-1]
        details[last_key] += " " + text
    else:
        details["その他"] = text


# ── 共同担保目録パーサー ──

def parse_kyoudou_tanpo(lines: list[str]) -> dict:
    result = {
        "記号番号": "",
        "調製": "",
        "担保一覧": [],
    }
    current = None
    header_seen = False

    for line in lines:
        line_n = normalize(line)

        if "記号及び番号" in line_n or ("記号" in line_n and "番号" in line_n):
            cols = split_row(line)
            for i, c in enumerate(cols):
                if "記号" in c and i + 1 < len(cols):
                    result["記号番号"] = clean_cell(cols[i + 1])
                if "調製" in c and i + 1 < len(cols):
                    result["調製"] = clean_cell(cols[i + 1])
            continue

        if not is_data_line(line):
            continue

        cols = split_row(line)
        joined = " ".join(cols)

        if "番 号" in joined and "担保の目的" in joined:
            header_seen = True
            continue

        if not header_seen:
            continue

        if len(cols) >= 4:
            bangou = clean_cell(cols[0])
            mokuteki = clean_cell(cols[1])
            juniban = clean_cell(cols[2])
            yobi = clean_cell(cols[3])

            if bangou:
                current = {
                    "番号": bangou,
                    "担保の目的": mokuteki,
                    "順位番号": juniban,
                    "予備": yobi,
                }
                result["担保一覧"].append(current)
            elif current:
                if mokuteki:
                    current["担保の目的"] += mokuteki
                if yobi:
                    current["予備"] += " " + yobi if current["予備"] else yobi

    return result


# ── メインパーサー ──

def parse_touki_pdf(pdf_path: str) -> dict:
    text = extract_text_from_pdf(pdf_path)
    sections = split_sections(text)

    result = {
        "ファイル名": pdf_path.split("/")[-1].split("\\")[-1],
        "ヘッダー": {},
        "種別": "",
        "表題部": {},
        "権利部_甲区": [],
        "権利部_乙区": [],
    }

    for section_name, section_lines in sections:
        if section_name == "header":
            result["ヘッダー"] = parse_header(section_lines)
        elif section_name == "表題部_土地":
            result["種別"] = "土地"
            result["表題部"] = parse_hyodaibu_tochi(section_lines)
        elif section_name == "表題部_建物":
            result["種別"] = "建物"
            result["表題部"] = parse_hyodaibu_tatemono(section_lines)
        elif section_name == "表題部_一棟":
            result["種別"] = "区分建物"
            result["一棟の建物の表示"] = parse_hyodaibu_ittou(section_lines)
        elif section_name == "表題部_敷地権目的":
            result["敷地権の目的である土地"] = parse_shikichiken_tochi(section_lines)
        elif section_name == "表題部_専有":
            result["専有部分の建物の表示"] = parse_hyodaibu_senyuu(section_lines)
        elif section_name == "表題部_敷地権":
            result["敷地権の表示"] = parse_shikichiken_hyoji(section_lines)
        elif section_name == "甲区":
            result["権利部_甲区"] = parse_kenribu(section_lines, "甲区")
        elif section_name == "乙区":
            result["権利部_乙区"] = parse_kenribu(section_lines, "乙区")
        elif section_name == "共同担保目録":
            result["共同担保目録"] = parse_kyoudou_tanpo(section_lines)

    return result


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    if len(sys.argv) < 2:
        print("Usage: python parser.py <pdf_path>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    result = parse_touki_pdf(pdf_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
