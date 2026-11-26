# -*- coding: utf-8 -*-
"""리로스쿨 상벌점 자동화 (디벗 미보관 → 구글 시트 → 리로스쿨).

이 폴더에는 코드만 둔다. 학생 학번·이름, 아이디·비밀번호, 토큰은 여기에 절대 두지 않는다.
  일반 설정   %LOCALAPPDATA%\\RiroMeritAutomation\\config.json   (주소·시트 ID 같은 비밀 아닌 값)
  인증정보    Windows 자격 증명 관리자 «riro-merit/<학교>.riroschool.kr»  (리로스쿨 아이디·비밀번호)
             %LOCALAPPDATA%\\RiroMeritAutomation\\secure\\*.dpapi          (구글 토큰 — 윈도우 DPAPI 로 암호화)
"""
