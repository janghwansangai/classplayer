import sys

if __name__ == "__main__":
    # 윈도우 업데이트: 받은 새 버전이 '바꿔 끼우기' 일만 하고 끝난다 (Qt 를 불러오지 않음)
    if len(sys.argv) >= 4 and sys.argv[1] == "--apply-update":
        from classplayer.update_apply import main as apply_main
        sys.exit(apply_main(sys.argv[2:]))
    from classplayer.app import main
    main()
