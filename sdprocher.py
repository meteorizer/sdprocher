#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
sdprocher.py - 프로세스 실행 상태 검사기
지정된 프로세스 목록에 대하여 현재 실행 상태를 점검하여 출력.

사용법:
    python sdprocher.py <입력파일.csv|json>
    cat procs.csv | python sdprocher.py
    python sdprocher.py --format json <입력파일>
"""
from __future__ import print_function

import sys
import os
import csv
import json
import argparse
import datetime
import io

# Windows 콘솔에서 UTF-8 I/O를 위해 인코딩 재설정
if sys.platform == 'win32':
    for _stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(_stream, 'reconfigure'):
            try:
                _stream.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[union-attr]
            except Exception:
                pass
    del _stream

try:
    import psutil
except ImportError:
    print("오류: psutil 패키지가 필요합니다. 설치: uv add psutil", file=sys.stderr)
    sys.exit(1)

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False


# ---------------------------------------------------------------------------
# 입력 파싱
# ---------------------------------------------------------------------------

def detect_format(content):
    """내용을 보고 JSON / CSV 자동 감지"""
    stripped = content.strip()
    if stripped.startswith(('[', '{')):
        return 'json'
    return 'csv'


def parse_json(content):
    data = json.loads(content)
    if isinstance(data, dict):
        data = [data]
    return data


def parse_csv(content):
    reader = csv.DictReader(io.StringIO(content))
    return list(reader)


# 허용하는 필드명 변형들
_FIELD_ALIASES = {
    'run type':     'run_type',
    'run_type':     'run_type',
    'type':         'run_type',
    'runtype':      'run_type',
    'process name': 'process_name',
    'process_name': 'process_name',
    'processname':  'process_name',
    'name':         'process_name',
    'cmd':          'cmd',
    'command':      'cmd',
    'cmdline':      'cmd',
    'path':         'path',
}


def normalize_record(record):
    """필드명 정규화 및 값 공백 제거"""
    out = {}
    for k, v in record.items():
        key = _FIELD_ALIASES.get(k.strip().lower(), k.strip().lower())
        out[key] = v.strip() if isinstance(v, str) else (v or '')
    return out


# ---------------------------------------------------------------------------
# 유틸리티
# ---------------------------------------------------------------------------

def _safe_str(s):
    """서로게이트 등 인코딩 불가 문자를 '?'로 대체하여 안전한 문자열 반환"""
    if not isinstance(s, str):
        return str(s) if s is not None else ''
    return s.encode('utf-8', errors='replace').decode('utf-8', errors='replace')


# ---------------------------------------------------------------------------
# 프로세스 검색
# ---------------------------------------------------------------------------

# cmdline의 첫 번째 토큰(실행 파일명)이 아래 목록에 해당하면 매칭에서 제외.
# 뷰어·에디터·페이저 등이 패턴 문자열을 인자로 받아 실행되는 경우를 걸러낸다.
_EXCLUDED_EXECUTABLES = {
    'vim', 'vi', 'nvim', 'nano', 'emacs',
    'less', 'more',
    'tail', 'head',
    'screen', 'tmux',
    'watch',
    'cat', 'grep', 'awk', 'sed',
}


def _is_excluded(cmdline_parts):
    """cmdline 첫 토큰의 basename이 제외 목록에 있으면 True"""
    if not cmdline_parts:
        return False
    exe_name = os.path.basename(cmdline_parts[0]).lower()
    # 확장자(.exe 등) 제거
    exe_name = os.path.splitext(exe_name)[0]
    return exe_name in _EXCLUDED_EXECUTABLES


def find_procs_by_cmd(cmd_pattern):
    """cmd_pattern 문자열을 포함하는 프로세스 목록 반환.
    뷰어·에디터·페이저 등의 명령어가 첫 토큰인 프로세스는 제외한다."""
    matches = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'status', 'create_time', 'exe']):
        try:
            parts = proc.info.get('cmdline') or []
            if _is_excluded(parts):
                continue
            cmdline = ' '.join(parts)
            if cmd_pattern and cmd_pattern in cmdline:
                matches.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return matches


def _fmt_ts(ts):
    """Unix timestamp → 'YYYY-MM-DD HH:MM:SS' 문자열"""
    try:
        return datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return ''


def get_proc_detail(proc):
    """psutil.Process 객체에서 필요한 정보 추출"""
    try:
        create_time = _fmt_ts(proc.create_time())
    except Exception:
        create_time = ''

    # 마지막 엑세스 시각: 실행 파일(exe)의 atime 사용
    access_time = ''
    try:
        exe = proc.exe()
        if exe and os.path.exists(exe):
            access_time = _fmt_ts(os.path.getatime(exe))
    except Exception:
        pass

    try:
        status = proc.status()
        is_zombie = (status == psutil.STATUS_ZOMBIE)
    except Exception:
        status = 'unknown'
        is_zombie = False

    try:
        cmdline = _safe_str(' '.join(proc.cmdline()))
    except Exception:
        cmdline = ''

    return {
        'pid':         proc.pid,
        'status':      status,
        'is_zombie':   is_zombie,
        'create_time': create_time,
        'access_time': access_time,
        'cmdline':     cmdline,
    }


# ---------------------------------------------------------------------------
# 점검 로직
# ---------------------------------------------------------------------------

def check_processes(process_list):
    """process_list 각 항목에 대해 실행 상태를 점검하여 결과 목록 반환"""
    results = []

    for raw in process_list:
        item = normalize_record(raw)
        run_type     = item.get('run_type', '')
        process_name = item.get('process_name', '')
        cmd          = item.get('cmd', '')
        path         = item.get('path', '')

        if not cmd:
            # cmd 없으면 검사 불가 — 미실행으로 기록
            results.append({
                'run_type':     run_type,
                'process_name': process_name,
                'running':      False,
                'zombie':       False,
                'create_time':  '',
                'access_time':  '',
                'cmd':          cmd,
                'path':         path,
                'pid':          '',
                'count':        0,
            })
            continue

        matched = find_procs_by_cmd(cmd)

        if not matched:
            results.append({
                'run_type':     run_type,
                'process_name': process_name,
                'running':      False,
                'zombie':       False,
                'create_time':  '',
                'access_time':  '',
                'cmd':          cmd,
                'path':         path,
                'pid':          '',
                'count':        0,
            })
        else:
            count = len(matched)
            for proc in matched:
                detail = get_proc_detail(proc)
                results.append({
                    'run_type':     run_type,
                    'process_name': process_name,
                    'running':      not detail['is_zombie'],
                    'zombie':       detail['is_zombie'],
                    'create_time':  detail['create_time'],
                    'access_time':  detail['access_time'],
                    'cmd':          detail['cmdline'] or cmd,
                    'path':         path,
                    'pid':          str(detail['pid']),
                    'count':        count,
                })

    return results


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------

# 테이블 출력용 헤더 (한글)
_TABLE_HEADERS = ['프로세스 타입', '프로세스명', '프로세스 상태', '좀비', '갯수', '실행 시각', '엑세스 시각', 'cmd']

# CSV / JSON 출력용 영문 키
_OUTPUT_KEYS = ['run_type', 'process_name', 'running', 'zombie', 'count', 'start_time', 'access_time', 'cmd']


def _to_output_record(r):
    """결과 dict → 영문 키 출력용 dict"""
    return {
        'run_type':     r['run_type'],
        'process_name': r['process_name'],
        'running':      'Y' if r['running'] else 'N',
        'zombie':       'Y' if r['zombie'] else 'N',
        'count':        r['count'],
        'start_time':   r['create_time'],
        'access_time':  r['access_time'],
        'cmd':          r['cmd'],
    }


def _table_row(r):
    return [
        r['run_type'],
        r['process_name'],
        'Y' if r['running'] else 'N',
        'Y' if r['zombie'] else 'N',
        str(r['count']),
        r['create_time'],
        r['access_time'],
        r['cmd'],
    ]


def output_rich(results):
    console = Console(legacy_windows=False)
    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", expand=False)

    table.add_column(_TABLE_HEADERS[0], style="dim", no_wrap=True)
    table.add_column(_TABLE_HEADERS[1], no_wrap=True)
    table.add_column(_TABLE_HEADERS[2], justify="center", no_wrap=True)
    table.add_column(_TABLE_HEADERS[3], justify="center", no_wrap=True)
    table.add_column(_TABLE_HEADERS[4], justify="right", no_wrap=True)
    table.add_column(_TABLE_HEADERS[5], no_wrap=True)
    table.add_column(_TABLE_HEADERS[6], no_wrap=True)
    table.add_column(_TABLE_HEADERS[7])

    for r in results:
        running_text = Text("Y", style="bold green") if r['running'] else Text("N", style="bold red")
        zombie_text  = Text("Y", style="bold yellow") if r['zombie'] else Text("N")
        count_text   = Text(str(r['count']), style="bold yellow") if r['count'] > 1 else Text(str(r['count']))
        table.add_row(
            r['run_type'],
            r['process_name'],
            running_text,
            zombie_text,
            count_text,
            r['create_time'],
            r['access_time'],
            r['cmd'],
        )

    console.print()
    console.print(table)
    running_cnt = sum(1 for r in results if r['running'])
    console.print(
        "  총 [bold]{0}[/bold]건 점검 완료  "
        "실행 중 [bold green]{1}[/bold green]건 / "
        "미실행 [bold red]{2}[/bold red]건".format(
            len(results), running_cnt, len(results) - running_cnt)
    )
    console.print()


def output_plain(results):
    col_widths = [len(h) for h in _TABLE_HEADERS]
    rows = [_table_row(r) for r in results]

    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))

    fmt = '  '.join('{{:<{}}}'.format(w) for w in col_widths)
    sep = '  '.join('-' * w for w in col_widths)

    print()
    print(fmt.format(*_TABLE_HEADERS))
    print(sep)
    for row in rows:
        print(fmt.format(*[str(c) for c in row]))
    print()
    running_cnt = sum(1 for r in results if r['running'])
    print("총 {0}건 점검 완료  실행 중 {1}건 / 미실행 {2}건".format(
        len(results), running_cnt, len(results) - running_cnt))
    print()


def output_csv(results):
    writer = csv.DictWriter(sys.stdout, fieldnames=_OUTPUT_KEYS, lineterminator='\n')
    writer.writeheader()
    for r in results:
        writer.writerow(_to_output_record(r))


def output_json(results):
    records = [_to_output_record(r) for r in results]
    print(json.dumps(records, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='프로세스 실행 상태 검사기',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            '예시:\n'
            '  python sdprocher.py procs.csv\n'
            '  python sdprocher.py procs.json\n'
            '  python sdprocher.py --format json procs.txt\n'
            '  python sdprocher.py -o csv procs.csv\n'
            '  python sdprocher.py -o json procs.csv\n'
        ),
    )
    parser.add_argument('input_file', help='입력 파일 경로 (CSV 또는 JSON)')
    parser.add_argument(
        '--format', '-f',
        choices=['csv', 'json'],
        help='입력 형식 강제 지정 (생략 시 자동 감지)',
    )
    parser.add_argument(
        '--output', '-o',
        choices=['table', 'csv', 'json'],
        default='table',
        help='출력 형식: table(기본값), csv, json',
    )
    args = parser.parse_args()

    # 입력 읽기
    try:
        with open(args.input_file, 'rb') as fh:
            content = fh.read().decode('utf-8')
    except IOError as e:
        print("오류: 파일을 열 수 없습니다 - {0}".format(e), file=sys.stderr)
        sys.exit(1)

    if not content.strip():
        print("오류: 입력이 비어 있습니다.", file=sys.stderr)
        sys.exit(1)

    # 형식 감지 및 파싱
    fmt = args.format or detect_format(content)
    try:
        process_list = parse_json(content) if fmt == 'json' else parse_csv(content)
    except Exception as e:
        print("오류: 입력 파싱 실패 ({0}) - {1}".format(fmt, e), file=sys.stderr)
        sys.exit(1)

    if not process_list:
        print("입력에 처리할 항목이 없습니다.", file=sys.stderr)
        sys.exit(0)

    results = check_processes(process_list)

    if args.output == 'csv':
        output_csv(results)
    elif args.output == 'json':
        output_json(results)
    elif HAS_RICH:
        output_rich(results)
    else:
        output_plain(results)


if __name__ == '__main__':
    main()
