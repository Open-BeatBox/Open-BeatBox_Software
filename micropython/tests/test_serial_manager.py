import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs"))
sys.path.insert(0, str(ROOT / "libs" / "beatbox-protocol"))

from libs.serial_manager import BeatBoxCommManager


def crc8(data, polynomial=0x07, init=0):
    if isinstance(data, str):
        data = data.encode()
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ polynomial) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def _wire_frame(command, data_type="s", seq=0, payload=""):
    body = f"{command}:{data_type}{seq:02x}{payload}"
    return f"{body}{crc8(body.encode()):02x}"


def _decode_queue(comm):
    return [item.decode() if isinstance(item, bytes) else item for item in comm.serial_manager.tx_queue]


def test_startup_frames_are_raw_r00():
    comm = BeatBoxCommManager()
    comm.send_startup_frames()
    sent = _decode_queue(comm)
    assert sent == ["starting:r00", "inited:r00"]


def test_set_command_sends_ack_then_done_and_updates_outputs():
    comm = BeatBoxCommManager()
    comm.handshake_status = comm.HANDSHAKE_DONE_STATUS

    # Word 0x0201 -> bit0(reward)=1, bit9(light)=1
    incoming = _wire_frame("set", data_type="x", seq=0, payload="0201")
    comm.process_message(incoming)

    sent = _decode_queue(comm)
    assert len(sent) == 2
    assert sent[0].startswith("ack:s00set_from00000000000")
    assert sent[1].startswith("done:s00")
    assert comm.emulator.get_output_pin_states_as_word() == "10000000010"


def test_crc_mismatch_signals_error_only():
    comm = BeatBoxCommManager()
    comm.handshake_status = comm.HANDSHAKE_DONE_STATUS

    valid = _wire_frame("set", data_type="x", seq=0, payload="0000")
    # Corrupt last CRC nibble
    invalid = valid[:-1] + ("0" if valid[-1] != "0" else "1")
    comm.process_message(invalid)

    sent = _decode_queue(comm)
    assert len(sent) == 1
    assert sent[0].startswith("error:s00CRC mismatch(")


def test_seq_mismatch_keeps_firmware_quirk_and_continues():
    comm = BeatBoxCommManager()
    comm.handshake_status = comm.HANDSHAKE_DONE_STATUS
    comm.seq_nb = 2

    incoming = _wire_frame("ready", data_type="s", seq=1, payload="")
    comm.process_message(incoming)

    sent = _decode_queue(comm)
    assert len(sent) == 3
    assert sent[0].startswith("error:s02Seq mismatch(")
    assert sent[1].startswith("ack:s01ready")
    assert sent[2].startswith("ready:sloop")


def test_input_change_sends_state_frame_with_seq():
    comm = BeatBoxCommManager()
    comm.on_input_pin_changes({"nose_poke": 1, "right_response": 1})

    sent = _decode_queue(comm)
    assert len(sent) == 1
    # bit0 and bit2 set => 0x05, framed as state:x00 + 05 + crc
    assert sent[0].startswith("state:x0005")
