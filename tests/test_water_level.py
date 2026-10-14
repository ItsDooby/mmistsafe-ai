import pytest
from mistsafe.database import init_db, get_device_calibration, set_device_calibration, update_simulator_config, get_device_state, save_telemetry
from mistsafe.validation import calculate_water_fill_percent, validate_telemetry_payload
from simulator import SimulatorState


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / 'level.db')
    init_db(path)
    return path


@pytest.mark.parametrize('percent,distance', [(0, 40), (25, 32.5), (50, 25), (75, 17.5), (100, 10)])
def test_periodic_packet_level_persistence(db, percent, distance):
    update_simulator_config('MISTSAFE-SIM-01', {'water_level_mode': str(percent)}, db_path=db)
    simulator = SimulatorState()
    for _ in range(2):
        packet = simulator.generate_packet(db_path=db)
        assert packet['water_distance_cm'] == distance
        save_telemetry(validate_telemetry_payload(packet), db_path=db)
    state = get_device_state('MISTSAFE-SIM-01', db_path=db)
    assert state['water_distance_cm'] == distance
    assert calculate_water_fill_percent(distance, 40, 10) == (percent, 'OK')


@pytest.mark.parametrize('mode,status', [('missing', 'UNKNOWN'), ('fault', 'FAULT')])
def test_unavailable_not_zero(db, mode, status):
    update_simulator_config('MISTSAFE-SIM-01', {'water_level_mode': mode}, db_path=db)
    packet = SimulatorState().generate_packet(db_path=db)
    assert packet['water_distance_cm'] is None
    assert packet['sensor_status']['water_distance'] == status


def test_calibration_isolation_and_persistence(db):
    assert get_device_calibration('MISTSAFE-ESP32-01', db_path=db) is None
    assert get_device_calibration('MISTSAFE-SIM-01', db_path=db)['empty_distance_cm'] == 40
    set_device_calibration('MISTSAFE-ESP32-01', 80, 15, db_path=db)
    init_db(db)
    assert get_device_calibration('MISTSAFE-ESP32-01', db_path=db)['full_distance_cm'] == 15
    assert get_device_calibration('MISTSAFE-SIM-01', db_path=db)['full_distance_cm'] == 10
    with pytest.raises(ValueError):
        set_device_calibration('MISTSAFE-ESP32-01', 10, 40, db_path=db)


def test_range_and_missing():
    assert calculate_water_fill_percent(None, 40, 10) == (None, 'LEVEL_UNAVAILABLE')
    assert calculate_water_fill_percent(25, None, None) == (None, 'CALIBRATION_REQUIRED')
    assert calculate_water_fill_percent(-1, 40, 10) == (None, 'INVALID_READING')
    assert calculate_water_fill_percent(float('nan'), 40, 10) == (None, 'INVALID_READING')
    assert calculate_water_fill_percent(55, 40, 10) == (-50, 'OUT_OF_RANGE')
