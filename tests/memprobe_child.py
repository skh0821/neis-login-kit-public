# -*- coding: utf-8 -*-
"""메모리 시험용 하위 프로세스 — 가상 비밀번호를 한 글자씩(표준입력 1바이트씩) 받아 금고에 넣고, 정리한 뒤 기다린다.

  mode=buffer : 새 방식 — SecretBuffer 에 한 자씩 → vault_write_buffer → wipe  (전체 str 을 만들지 않음)
  mode=str    : 예전 방식 — 글자를 이어 붙인 str → keyring.set_password → 참조 끊기(None)
  mode=reveal : 로그인 쪽 — 금고에서 str 로 꺼냈다가(VaultSecret.reveal) 참조 끊기
부모(시험)가 이 프로세스의 메모리에서 가상 비밀번호가 몇 군데 남았는지 «개수만» 센다.
"""
import gc
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import secure_store as S  # noqa: E402

mode, svc = sys.argv[1], sys.argv[2]
user = "fake-user"
if mode == "reveal":
    t = S.VaultSecret(svc, user)
    secret = t.reveal()
    ok = len(secret) > 0
    secret = None
elif mode == "buffer":
    with S.SecretBuffer() as buf:
        while True:
            b = os.read(0, 1)
            if not b or b == b"\n":
                break
            buf.append(chr(b[0]))
        S.vault_write_buffer(svc, user, buf)
    ok = buf.is_wiped()
else:
    chars = []
    while True:
        b = os.read(0, 1)
        if not b or b == b"\n":
            break
        chars.append(chr(b[0]))
    s = "".join(chars)
    import keyring
    keyring.set_password(svc, user, s)
    chars = s = None
    ok = True
gc.collect()
print("READY" if ok else "NOT_WIPED", flush=True)
os.read(0, 1)            # 부모가 메모리를 센 뒤 신호를 줄 때까지 기다린다
