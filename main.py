import sys


def main() -> None:
    try:
        from glm_tui.app import run
    except ModuleNotFoundError as exc:
        missing = exc.name or "依赖"
        print(
            f"缺少依赖：{missing}\n"
            "请先运行：python3 -m pip install -r requirements.txt\n"
            "或直接运行：./run.sh",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    run()


if __name__ == "__main__":
    main()
