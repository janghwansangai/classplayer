# vendor

- `yt_dlp_plugins/extractor/getpot_bgutil*.py`: [bgutil-ytdlp-pot-provider-rs](https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs) v0.8.1 의 yt-dlp 플러그인(GPL-3.0). 수정하지 않았다.
  실행 파일 `bgutil-pot` 은 저장소에 넣지 않고, 빌드 때 같은 릴리스에서 받아 앱 번들에 넣는다(`packaging/fetch_bgutil.sh`, CI).
  유튜브 어린이용 영상처럼 PO 토큰이 없으면 403 이 나는 영상을 재생·저장하기 위해 쓴다.
