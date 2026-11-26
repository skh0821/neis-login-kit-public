#!/usr/bin/env python3
"""인증서 이름·비밀번호만 다시 설정한다 (학년·반·캘린더 설정은 그대로 유지).

비밀번호는 화면에 표시되지 않고, 파일이 아니라
Windows 자격 증명 관리자(keyring)에만 저장된다.

  사용법:  python fix_cert.py
"""
import sys

import keyring

import teacher_config

try:
    cfg = teacher_config.load_config()
except teacher_config.ConfigMissingError as e:
    print(f"❌  {e}")
    sys.exit(1)

print(f"현재 인증서 이름: {teacher_config.mask_name(cfg['cert_name']) or '(없음)'}")
print("나이스 '인증서 로그인' 창에 뜨는 이름을 그대로 적으세요.")
print("(그대로 두려면 그냥 Enter)  ※ 마우스로 하려면: python secure_settings.py")

cert_name = input("새 인증서 이름 [그대로]: ").strip() or cfg["cert_name"]
if not cert_name:
    print("❌  인증서 이름이 비어 있습니다.")
    sys.exit(1)

import secure_store as SS  # noqa: E402

# 지울 수 있는 버퍼에 받아 금고에 바로 넣고 0 으로 덮는다 (전체 비밀번호 str 을 만들지 않는다)
with SS.SecretBuffer() as buf:
    if not SS.read_secret_console("인증서 비밀번호 (화면에 표시되지 않습니다 · Esc = 취소): ", buf) or not len(buf):
        print("❌  비밀번호를 넣지 않아 끝냅니다 (바뀐 것 없음).")
        sys.exit(2)
    try:
        SS.vault_write_buffer(teacher_config.KEYRING_SERVICE, cert_name, buf)
    except SS.SecureError as e:
        print(f"❌  금고에 저장하지 못했습니다 ({e.code}).")
        sys.exit(1)
cfg["cert_name"] = cert_name
teacher_config.save_config(cfg)

print(f"\n✅  완료. 인증서 이름 -> '{teacher_config.mask_name(cert_name)}'")
print("   비밀번호는 Windows 자격 증명 관리자에 저장되었습니다.")
