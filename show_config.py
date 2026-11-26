#!/usr/bin/env python3
"""저장된 설정을 «가려서» 보여준다.

설정 파일을 cat 으로 통째로 찍으면 연락처·인증서 이름이 클로드 대화에 그대로 남는다
(2026-09-16 리허설에서 실제로 새어 나갔다). 확인은 이 스크립트로 한다.

    python show_config.py

2026-11-09: 개인 설정은 있다/없다만 보여 준다 (끝자리도 찍지 않는다). 보호 저장 상태를 함께 보여 준다.
자세한 보호 상태 점검은  python secure_check.py
"""
import sys

import keyring

import teacher_config


def mask_phone(v: str) -> str:
    """예전 함수 이름 유지 — 이제는 «있음»만."""
    return "있음" if v else ""


def mask_name(v: str) -> str:
    return teacher_config.mask_name(v)


def main() -> int:
    try:
        cfg = teacher_config.load_config()
    except teacher_config.ConfigMissingError as e:
        print(f"❌  {e}")
        return 1

    split = teacher_config.is_split_mode()
    print("📋  저장된 설정 (개인정보는 있다/없다만 보여줍니다)")
    print(f"    보호 저장     : {'개인 설정 암호화 ✅' if split else '❌ 아직 평문 — python secure_migrate.py run'}")
    print(f"    나이스 주소   : {cfg.get('neis_url', '')}")
    print(f"    접속 방식     : {'EVPN' if cfg.get('access_mode') == 'evpn' else ('학교 업무망' if cfg.get('access_mode') == 'school_net' else '(안 정함)')}")
    print(f"    담임 학급     : {'설정됨' if cfg.get('grade') and cfg.get('class') else '(없음)'}")
    print(f"    인증서 이름   : {'설정됨 ✅' if cfg.get('cert_name') else '(없음)'}")
    print(f"    연락처        : {'설정됨 ✅' if cfg.get('contact') else '(안 넣음)'}")
    print(f"    개인결재선    : {'설정됨 ✅' if cfg.get('approval_line') else '(안 넣음)'}")
    print(f"    출근·퇴근     : {'설정됨 ✅' if cfg.get('work_start') and cfg.get('work_end') else '(기본값)'}")
    print(f"    우리 학교     : {'설정됨 ✅' if cfg.get('school_name') else '(안 넣음 — 휴업일 확인 안 함)'}")
    print(f"    구글 토큰     : {dict(protected='암호화 ✅', plain='❌ 평문 token.json', none='(연결 안 함)')[teacher_config.google_token_state()]}")

    import secure_store as SS        # 있다/없다만 — 금고에서 비밀번호 내용을 꺼내지 않는다
    pw = bool(cfg.get("cert_name")) and SS.vault_exists(teacher_config.KEYRING_SERVICE, cfg["cert_name"])
    print(f"    인증서 비밀번호: {'저장됨 ✅ (Windows 자격 증명 관리자)' if pw else '❌ 없음 — 보안 설정창에서 넣으세요'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
