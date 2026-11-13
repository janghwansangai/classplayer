#!/bin/bash
# 테스트용 영상 생성 (ffmpeg 필요)
set -e
cd "$(dirname "$0")/assets"
FONT="/System/Library/Fonts/Supplemental/Arial.ttf"
# 1) 1080p H.264 10초
[ -f test_h264_1080p.mp4 ] || ffmpeg -y -v error -f lavfi -i "testsrc2=size=1920x1080:rate=30:duration=10" -f lavfi -i "sine=frequency=440:duration=10" -c:v libx264 -preset veryfast -pix_fmt yuv420p -c:a aac test_h264_1080p.mp4
# 2) 4K HEVC 10초 (VideoToolbox 인코더)
[ -f test_hevc_4k.mp4 ] || ffmpeg -y -v error -f lavfi -i "testsrc2=size=3840x2160:rate=30:duration=10" -c:v hevc_videotoolbox -b:v 20M -tag:v hvc1 -pix_fmt yuv420p test_hevc_4k.mp4
# 3) 라벨이 붙은 등장방형 360도 테스트 영상 (4096x2048) — 이미지는 Qt로 생성
[ -f equirect_labels.png ] || ../../.venv/bin/python ../make_equirect_png.py equirect_labels.png
[ -f test_360_equirect.mp4 ] || ffmpeg -y -v error -loop 1 -i equirect_labels.png -t 6 -r 30 -c:v libx264 -preset veryfast -pix_fmt yuv420p test_360_equirect.mp4
# 4) 같은 영상을 유튜브 EAC 큐브맵으로 변환
[ -f test_360_eac.mp4 ] || ffmpeg -y -v error -i test_360_equirect.mp4 -vf "v360=e:eac" -c:v libx264 -preset veryfast -pix_fmt yuv420p test_360_eac.mp4
# 5) 상하 분할 입체 (위/아래 동일 복제)
[ -f test_360_tb.mp4 ] || ffmpeg -y -v error -i test_360_equirect.mp4 -filter_complex "[0:v]split[a][b];[a][b]vstack" -c:v libx264 -preset veryfast -pix_fmt yuv420p test_360_tb.mp4
ls -la
