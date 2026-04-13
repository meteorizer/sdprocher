# sdprocher

지정된 프로세스 목록을 받아 현재 실행 상태를 점검하고 출력하는 단일 파일 CLI 도구.

## 요구 사항

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (패키지 관리)

의존 패키지는 `uv sync`로 설치된다.

```
psutil   — 프로세스 정보 수집
rich     — 컬러 테이블 출력 (없으면 plain text로 자동 폴백)
```

## 설치

```bash
git clone <repo>
cd sdprocher
uv sync
```

## 사용법

```
python sdprocher.py [옵션] [입력파일]
```

### 인수

| 인수 / 옵션 | 설명 |
|---|---|
| `입력파일` | CSV 또는 JSON 파일 경로. 생략 시 stdin에서 읽음 |
| `-f`, `--format {csv,json}` | 입력 형식 강제 지정 (생략 시 자동 감지) |
| `-o`, `--output {table,csv,json}` | 출력 형식 (기본값: `table`) |

### 실행 예시

```bash
# 파일 입력
uv run python sdprocher.py procs.csv
uv run python sdprocher.py procs.json

# 파이프 입력
cat procs.csv | uv run python sdprocher.py

# 출력 형식 지정
uv run python sdprocher.py -o csv  procs.csv
uv run python sdprocher.py -o json procs.csv

# 입력 형식 강제 지정 (확장자가 없을 때)
uv run python sdprocher.py -f json procs.txt

# 파이프라이닝
uv run python sdprocher.py -o json procs.csv | jq '.[] | select(.running == "N")'
```

## 입력 형식

CSV와 JSON 두 가지를 지원한다. 첫 글자가 `[` 또는 `{`이면 JSON, 그 외에는 CSV로 자동 판별한다.

### 필드

| 필드 | 설명 | 필수 |
|---|---|:---:|
| `run_type` | 프로세스 실행 타입 (python, java, nginx 등) | |
| `process_name` | 프로세스 명칭 | ✓ |
| `cmd` | 실행 중인 프로세스의 cmdline에서 검색할 문자열 | ✓ |
| `path` | 프로세스 경로 (참고용) | |

필드명은 공백·언더스코어 혼용 및 일부 축약형을 허용한다.

| 입력에서 허용하는 변형 | 정규화 결과 |
|---|---|
| `run type`, `run_type`, `type`, `runtype` | `run_type` |
| `process name`, `process_name`, `name` | `process_name` |
| `cmd`, `command`, `cmdline` | `cmd` |

### CSV 예시

```csv
run_type,process_name,cmd,path
nginx,NginX,nginx: master process,/usr/sbin/nginx
python,블랙홀 차단 데몬,python bblock.py --superman,/opt/bblock
java,결제 서버,com.example.PaymentServer,/opt/payment
```

### JSON 예시

```json
[
  {
    "run_type": "nginx",
    "process_name": "NginX",
    "cmd": "nginx: master process",
    "path": "/usr/sbin/nginx"
  },
  {
    "run_type": "python",
    "process_name": "블랙홀 차단 데몬",
    "cmd": "python bblock.py --superman",
    "path": "/opt/bblock"
  }
]
```

## 출력 형식

### table (기본값)

`rich` 패키지가 있으면 컬러 테이블, 없으면 plain text 테이블로 출력된다.

```
╭──────────────┬──────────────────┬──────────────┬────┬──────┬─────────────────────┬─────────────────────┬────────────────────────────────╮
│ 프로세스 타입 │ 프로세스명       │ 프로세스 상태 │ 좀비 │ 갯수 │ 실행 시각           │ 엑세스 시각         │ cmd                            │
├──────────────┼──────────────────┼──────────────┼────┼──────┼─────────────────────┼─────────────────────┼────────────────────────────────┤
│ nginx        │ NginX            │      Y       │ N  │    1 │ 2026-02-22 15:00:20 │ 2026-04-13 14:22:33 │ nginx: master process ...      │
│ python       │ 블랙홀 차단 데몬 │      N       │ N  │    0 │                     │                     │ python bblock.py --superman    │
╰──────────────┴──────────────────┴──────────────┴────┴──────┴─────────────────────┴─────────────────────┴────────────────────────────────╯
  총 2건 점검 완료  실행 중 1건 / 미실행 1건
```

- 프로세스 상태: 실행 중 **Y**(녹색) / 미실행 **N**(빨간색)
- 좀비: 좀비 프로세스이면 **Y**(노란색)
- 갯수: 포크된 인스턴스가 2개 이상이면 노란색으로 강조

### CSV 출력 (`-o csv`)

```csv
run_type,process_name,running,zombie,count,start_time,access_time,cmd
nginx,NginX,Y,N,1,2026-02-22 15:00:20,2026-04-13 14:22:33,nginx: master process /usr/sbin/nginx
python,블랙홀 차단 데몬,N,N,0,,,python bblock.py --superman
```

### JSON 출력 (`-o json`)

```json
[
  {
    "run_type": "nginx",
    "process_name": "NginX",
    "running": "Y",
    "zombie": "N",
    "count": 1,
    "start_time": "2026-02-22 15:00:20",
    "access_time": "2026-04-13 14:22:33",
    "cmd": "nginx: master process /usr/sbin/nginx"
  },
  {
    "run_type": "python",
    "process_name": "블랙홀 차단 데몬",
    "running": "N",
    "zombie": "N",
    "count": 0,
    "start_time": "",
    "access_time": "",
    "cmd": "python bblock.py --superman"
  }
]
```

### 출력 필드 설명

| 필드 (CSV/JSON) | 테이블 헤더 | 설명 |
|---|---|---|
| `run_type` | 프로세스 타입 | 입력의 run_type 값 |
| `process_name` | 프로세스명 | 입력의 process_name 값 |
| `running` | 프로세스 상태 | 실행 중 Y / 미실행 N |
| `zombie` | 좀비 | 좀비 프로세스 여부 Y/N |
| `count` | 갯수 | 매칭된 프로세스 인스턴스 수 (포크 포함) |
| `start_time` | 실행 시각 | 프로세스 최초 실행 시각 |
| `access_time` | 엑세스 시각 | 실행 파일(exe)의 마지막 접근 시각 |
| `cmd` | cmd | 실제 프로세스 cmdline |

## 동작 방식

`cmd` 필드의 문자열을 모든 실행 중인 프로세스의 cmdline에서 부분 일치 검색한다.  
매칭된 프로세스가 없으면 미실행(running=N, count=0)으로 기록한다.  
매칭된 프로세스가 여러 개이면 각각 별도 행으로 출력하고, `count`에 총 인스턴스 수를 표시한다.

### 제외 명령어

다음 명령어가 cmdline의 첫 토큰인 프로세스는 매칭에서 제외한다.  
뷰어·에디터·페이저 등이 점검 대상 패턴을 인자로 포함하더라도 오탐되지 않도록 하기 위함이다.

| 분류 | 명령어 |
|---|---|
| 에디터 | `vim`, `vi`, `nvim`, `nano`, `emacs` |
| 페이저 | `less`, `more` |
| 파일 뷰어 | `tail`, `head`, `cat` |
| 터미널 멀티플렉서 | `screen`, `tmux` |
| 반복 실행 | `watch` |
| 텍스트 처리 | `grep`, `awk`, `sed` |

목록을 수정하려면 `sdprocher.py` 내 `_EXCLUDED_EXECUTABLES` 집합을 편집한다.
