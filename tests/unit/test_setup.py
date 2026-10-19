from pychain import config


def test_protocol_constants_are_available() -> None:
    assert config.BLOCK_REWARD == 50
    assert config.TARGET_BLOCK_TIME == 10
    assert config.RETARGET_INTERVAL == 10
    assert config.INITIAL_DIFFICULTY_BITS == 16
    assert config.MAX_TX_PER_BLOCK == 100
    assert config.MAX_FUTURE_DRIFT == 120
    assert config.MAX_PEERS == 8
