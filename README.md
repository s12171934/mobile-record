---
title: mobile-record
created: 2026-11-13
updated: 2026-11-13
tags: [domain/인공지능, area/ai에이전트, type/절차, agent/codex-01a1d27e-1212-7c83-bade-774f188a76ef]
source: ai
---

# mobile-record

> 기본은 iOS 시뮬레이터·Android 에뮬레이터, 실기기 전용 조건일 때만 iOS 실기기에서 네이티브 탭·화면 키보드 입력을 수행하고 DeviceKit MJPEG 영상을 MP4로 남기는 스킬이다. 에이전트로 먼저 탐색하고 반복 구간을 스크립트로 보관한다.

- 처음에는 에이전트가 직접 탐색·검증하고, 반복해서 확인하는 구간을 시나리오로 보관한다.
- 이후에는 초기 상태를 확인해 재사용하고, 화면이 바뀌거나 실패하면 에이전트 관찰로 돌아가 케이스를 갱신한다.
- Mac GUI 조작을 사용하지 않는다.

## 사용 절차

- [[SKILL|사용 절차]]
- [[references/에뮬레이터|에뮬레이터 실측·녹화 (기본 경로)]]
- [[references/연결과 디스플레이|러너 연결·활성 디스플레이·복구]]
- [[references/시나리오 실행|시나리오 자동 실행·속도 조절]]

## 설치와 의존성

- [mobile-record 저장소](https://github.com/s12171934/mobile-record)를 clone하고 이 폴더를 에이전트의 skills 디렉터리에 링크한다.
- Xcode, Python 3, ffmpeg/ffprobe가 필요하다.
- [DeviceKit iOS](https://github.com/mobile-next/devicekit-ios)의 기존에 서명된 테스트 빌드와 페어링된 실기기가 필요하다.
- [agent-dashboard](https://github.com/s12171934/agent-dashboard)의 공개 `bin/atest` CLI가 필요하다.
- 기기·계정·서명 정보는 실행 인자로 받는다. 영상·세션·앱별 테스트 케이스는 이 저장소에 포함하지 않는다.
- 공개 진입점은 스킬 이름 `mobile-record`다. `scripts/`와 `assets/`는 내부 구현이며 외부 도구가 직접 의존하는 API가 아니다.

## 커밋 전 점검과 출처

- `gitleaks` CLI를 PATH에 설치하고 `git config core.hooksPath .githooks`로 커밋 전 비밀 검사를 활성화한다.
- DeviceKit 보정 패치의 출처와 라이선스는 [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt)와 [동봉한 원본 라이선스](assets/DEVICEKIT_LICENSE.txt)를 따른다.
