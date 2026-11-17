---
name: mobile-record
description: 모바일 앱 화면을 네이티브 탭과 화면 키보드 키로 조작하고 MP4로 녹화·검증한다. 다른 세션이 쓰지 않는 실기기가 있으면 그 기기를 먼저 쓰고(iOS는 DeviceKit XCTest 러너에 직접 연결해 MJPEG 스트림 녹화), 비어 있는 실기기가 없으면 iOS 시뮬레이터·Android 에뮬레이터를 쓴다. "모바일 실측", "에뮬레이터·시뮬레이터에서 확인하면서 녹화", "실기기 테스트하면서 녹화", "아이폰 네이티브 조작", "키보드 UI로 직접 입력", "Mobile MCP를 쓰려던 경우"에 사용한다. Mobile MCP는 쓰지 않는다. 브라우저 에뮬레이션(데스크톱 브라우저 모바일 크기)은 범위 밖이다.
---

# 모바일 실측

**비어 있는 실기기를 먼저 쓰고, 없으면 에뮬레이터를 쓴다.** 시작 전에 `python3 "$SKILL/scripts/free_devices.py"`로 연결된 실기기와 점유 여부를 본다. 다른 세션의 러너·녹화 락이 잡혀 있거나 그 기기 ID를 인자로 든 프로세스(xcodebuild·adb -s 등)가 있으면 사용 중이다. `"free": true`인 기기가 있으면 그 기기로 실측한다. 실기기 실측을 기다려야 하면 별도 터미널 세션에 `python3 "$SKILL/scripts/free_devices.py" --acquire ios|android|any --label <티켓 키>`를 띄운다 — 세션 공용 FIFO 대기열에 서서 기기가 풀리면 `{"acquired": …}`를 출력하고 그 기기를 점유한다. 끝나면 그 터미널에 `q`를 보내 다음 대기자에게 넘긴다. `--queue`는 대기 중인 세션을 보여 준다. iOS는 아래 본문(DeviceKit)을, Android는 [에뮬레이터 안내](references/에뮬레이터.md)의 `adb -s <serial>` 명령을 쓰고, launch_runner를 거치지 않는 동안에는 `free_devices.py --hold <기기 ID>`를 별도 터미널 세션에 띄워 점유를 표시한다. 비어 있는 실기기가 없을 때만 iOS 시뮬레이터·Android 에뮬레이터를 쓴다. 다른 세션이 쓰는 기기를 빼앗거나 그 러너에 끼어들지 않는다. Mobile MCP는 쓰지 않는다.

**처음에는 에이전트가 직접 동작시키고, 자주 확인하는 흐름은 검증된 시나리오로 보관해 재사용한다.** 별도 관찰자가 기기 프레임을 녹화하며, 화면 이동·입력·복귀를 실제 표시 결과로 판정한다.

## 기본 흐름: 탐색 → 반복 구간 저장 → 재사용

1. **에이전트로 먼저 실행한다.** 새 화면이나 미확인 흐름은 snapshot/tap/key로 관찰 → 동작 → 재관찰한다. 실제 경로·분기·기대 결과·복구 방법을 확인한다.
2. **반복되는 부분만 스크립트로 만든다.** 같은 확인이 반복되거나 다시 쓸 것이 분명하면, 통과한 경로에서 안정적인 부분을 JSON 시나리오로 추린다. 실행 로그 전체를 그대로 재생하지 않는다. 상황 판단이 필요한 분기는 에이전트가 계속 맡는다.
3. **재생 검증 후 보관한다.** 같은 초기 상태에서 시나리오를 실행하고 화면·입력값·복귀를 확인한다. 앱별 케이스는 프로젝트의 기존 테스트/시나리오 폴더에, 이번 실행의 영상·로그는 run 폴더에 둔다. 임시 녹화 폴더만을 재사용 원본으로 삼지 않는다. 공통 실행기는 이 스킬이 소유한다.
4. **다음 작업에서 찾아 쓴다.** 보관한 케이스의 목적·전제 조건·최근 통과 근거를 확인하고, 현재 초기 화면이 맞으면 재사용한다. 실패하거나 화면·앱 버전·키 배치가 달라졌다면 에이전트 관찰로 돌아가 원인을 확인하고, 수정한 케이스를 다시 검증해 갱신한다.

보관 항목과 자동 실행·속도 설정은 [시나리오 실행 안내](references/시나리오%20실행.md)를 읽는다. 검증된 고정 키 배치는 연속 키 탭 후 최종 입력값을 확인할 수 있다. 속도는 네이티브 동작 간격이며 영상 재생 속도와 별개다.

## 전제와 범위 (iOS 실기기)

- Mac의 Xcode/devicectl, Python 3, ffmpeg/ffprobe, 유효하게 서명된 DeviceKit `build-for-testing` 산출물과 `.xctestrun`, agent-dashboard의 공개 `bin/atest` CLI가 필요하다.
- 기기는 기존에 페어링되어 있고 Developer Mode 및 UI 자동화가 허용되어 있어야 한다. Wi-Fi는 이 조건의 paired tunnel에서 실측됐다. USB 없는 초기 설정까지 검증한 것은 아니다.
- Computer Use·Device Hub·권한 변경을 기본 경로로 쓰지 않는다. 사용자가 정한 접기·회전 조건을 유지한다. 실제 생체 인증이나 잠금 해제가 필요하면 사용자가 기기에서 수행해야 한다.
- Mobile MCP의 탐색 성공과 devicectl 연결 성공은 별개다. 이 스킬의 경로는 **DeviceKit 직접 RPC**이며 Mobile MCP 연결 성공이라고 보고하지 않는다.
- 한 기기의 케이스는 순차 실행한다. 전체 기기 화면이 대상이다. 다른 사람의 조작도 영상에 들어올 수 있다.
- 다중 화면의 탭은 [연결 안내](references/연결과%20디스플레이.md)의 보정을 요구한다. 다른 액션도 같은 display를 지원한다고 가정하지 않는다.

## 1. 케이스와 연결

요청을 **초기 상태 → 동작 → 기대 화면 → 복구**로 정한다. 기기 ID·서명 팀·터널 IP·계정·앱 주소는 실행 인자로 받고 스킬에 넣지 않는다. 러너의 준비·복구·보정은 [references/연결과%20디스플레이.md](references/연결과%20디스플레이.md)를 읽는다.

이미 실행 중인 러너는 소유 세션에 확인해 연결한다. 새 실행은 아래 CLI를 터미널 세션으로 유지한다. 변수들은 이번 실행에서 확인한 절대 경로/값이다.

```bash
python3 "$SKILL/scripts/launch_runner.py" --device "$DEVICE" --xctestrun "$XCTESTRUN" --out "$RUNNER_OUT" --atest "$ATEST"
```

서버 준비 로그와 `/health`를 확인하고 출력된 endpoint에 연결한다. `--display-tap-supported`는 보정된 빌드를 확인한 경우에만 쓴다. 미지원 필드를 무시하는 러너에 `dryRun`을 보내면 실제 탭이 될 수 있으므로 임의 탐침으로 쓰지 않는다.

```bash
python3 "$SKILL/scripts/mobile_record.py" --run "$RUN" init --endpoint "$ENDPOINT" --device "$DEVICE" --display-tap-supported
python3 "$SKILL/scripts/mobile_record.py" --run "$RUN" snapshot --name initial
```

스크린샷을 직접 보고 foreground 앱과 목표 화면을 확인한다. URL 열기 응답의 success만으로 전환을 판정하지 않는다. 필요하면 `xcrun devicectl device process launch --device "$DEVICE" "$BUNDLE_ID"`로 대상 앱을 활성화하고 다시 관찰한다.

## 2. 녹화와 네이티브 동작

별도 터미널 세션에서 녹화한다. 회전은 실제 MJPEG 프레임과 스크린샷을 비교해 `none/cw/ccw/half` 중 선택한다. 가로 화면이라고 항상 `cw`를 쓰지 않는다. 첫 프레임의 방향·대상을 확인한 뒤 동작을 시작한다.

```bash
python3 "$SKILL/scripts/mobile_record.py" --run "$RUN" record --rotation cw --limit 180
```

기본 약 5fps, 50% 크기의 무음 영상이다. UI 스냅샷 수집 동안 프레임 간격이 늘 수 있다. OS의 screen-record capability가 없어도 MJPEG 경로는 별도로 동작할 수 있다. Mac 화면 녹화 권한은 쓰지 않는다.

각 단계는 **관찰 → 동작 → 재관찰 → 판정**으로 진행한다. `atest start --kind manual --label ... 케이스...`로 시작하고 `atest case ... running|passed|failed`, 마지막에 `atest end`로 기록한다.

```bash
python3 "$SKILL/scripts/mobile_record.py" --run "$RUN" snapshot --name before
python3 "$SKILL/scripts/mobile_record.py" --run "$RUN" tap --x 100 --y 200
python3 "$SKILL/scripts/mobile_record.py" --run "$RUN" snapshot --name after
python3 "$SKILL/scripts/mobile_record.py" --run "$RUN" note --say '기대 문구와 복귀 버튼 확인'
```

위 좌표는 문법 예시다. 실제 좌표는 최신 screenshot과 `*.summary.json`에서 구한다. AX 중심점에서 해당 window 원점을 뺀 point 좌표를 전달한다. 픽셀로 골랐다면 실제 pointScale로 나눈다. `displayId`와 screenshot용 `uniqueId`를 혼동하지 않는다. 러너가 회전을 처리하므로 재회전한 좌표를 넣지 않는다. 접힘·회전·foreground 변경 후 다시 캡처한다. 화면 밖·가려진 요소를 누르지 않는다.

## 3. 키보드 UI 입력

키보드 검증은 **화면 글자 키 직접 탭**으로 수행한다. `device.io.text`, pasteboard, DOM 대입, `typeText` 등 문자열 주입으로 대체하지 않는다.

1. 입력 필드를 탭하고 캡처한다. 의도한 필드의 focus, 키보드 표시, 언어·배치를 확인한다.
2. 현재 keyboard subtree의 키 label로 한 키씩 누른다. 수동 `key`는 실제 `device.io.tap`을 호출하고 결과를 자동 캡처한다. 자동 실행은 `each` 모드에서 매 키의 조합값을 확인하고, 명시적으로 고정 배치를 확인한 `batch` 모드에서는 마지막 입력값을 확인한다.

```bash
python3 "$SKILL/scripts/mobile_record.py" --run "$RUN" key --label 'ㄱ'
python3 "$SKILL/scripts/mobile_record.py" --run "$RUN" key --label 'ㅏ'
python3 "$SKILL/scripts/mobile_record.py" --run "$RUN" key --label 'ㄴ'
python3 "$SKILL/scripts/mobile_record.py" --run "$RUN" key --label 'ㅏ'
```

3. 화면과 focused AX value가 `가나`인지 확인한다. 언어가 다르면 현재 화면의 전환 키를 탭하고 다시 캡처한다. 키가 없거나 중복되면 화면에서 대상을 다시 고른다.
4. 요청된 경우에만 검색·전송·완료를 누른다. 입력만 검증하는 케이스는 취소하거나 안전한 바깥 영역을 탭한다. 키보드가 사라지고 기존 페이지가 유지되는지 확인한다.

## 4. 종료와 산출물

관찰자 터미널에 `q`를 보내 MP4를 마감한다. 그 후 무결성 검사를 실행한다.

```bash
"$ATEST" run --label '모바일 MP4 검증' -- python3 "$SKILL/scripts/mobile_record.py" --run "$RUN" finish
```

`finish`는 전체 디코딩, ffprobe, head/tail PNG, `video.timeline.txt`, `video.actions.json`, 메타데이터를 만든다. head/tail과 핵심 동작 프레임을 직접 열어 방향·대상·입력값을 확인한다. 타임라인은 녹화 명령 시작의 wall clock 기준 근사치다. MP4 검사와 제품 케이스 통과를 구분한다.

자신이 시작한 러너만 `POST /shutdown`으로 종료하고 XCTest 종료 결과까지 확인한다. 공유 러너는 소유 세션에 맡긴다. 녹화와 러너를 남겨두지 않는다.

최종 응답은 케이스별 결과, 실제 입력 방식, 영상·스크린샷 링크, 미검증 범위를 담는다. 개인 정보를 공개 스킬에 넣지 않는다. 대시보드는 설치된 `amedia add <video>`와 기존 허용 경로를 사용한다. 이 CLI는 허용 녹화 폴더 바로 아래의 MP4를 요구하므로 하위 run 폴더의 영상은 timeline/actions sidecar와 함께 배치한다. 볼트 저장 요청에는 `vault-entry`, 새 프로젝트 노트에는 `vault-project-doc`를 적용한다.
