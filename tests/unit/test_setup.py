from pychain import config
from scripts.run_demo import DEFAULT_DATA_ROOT, ROOT, build_parser


def test_protocol_constants_are_available() -> None:
    assert config.BLOCK_REWARD == 50
    assert config.TARGET_BLOCK_TIME == 10
    assert config.RETARGET_INTERVAL == 10
    assert config.INITIAL_DIFFICULTY_BITS == 16
    assert config.MAX_TX_PER_BLOCK == 100
    assert config.MAX_FUTURE_DRIFT == 120
    assert config.MAX_PEERS == 8


def test_demo_defaults_to_a_fresh_versioned_data_directory() -> None:
    arguments = build_parser().parse_args([])

    assert DEFAULT_DATA_ROOT == ROOT / "data" / "demo-v1"
    assert arguments.data_root == DEFAULT_DATA_ROOT


def test_demo_accepts_custom_data_directory() -> None:
    arguments = build_parser().parse_args(["--data-root", "custom-demo"])

    assert arguments.data_root.name == "custom-demo"
