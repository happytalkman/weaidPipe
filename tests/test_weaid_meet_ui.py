"""Regression checks for the browser meeting controls."""

import ast
import shutil
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "weaid_meet.py"


@pytest.fixture
def room_script():
    tree = ast.parse(SOURCE.read_text())
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_MEET_ROOM_HTML"
            for target in statement.targets
        ):
            html = ast.literal_eval(statement.value)
            return html.split("<script>")[1].split("</script>")[0]
    pytest.fail("Meeting room script is missing")


def run_browser_logic(script, checks):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for browser-script regression checks")
    setup = """
const assert = require('node:assert/strict');
const elements = new Map();
global.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, {
      style:{}, classList:{toggle(){}}, setAttribute(){}, replaceChildren(){},
      appendChild(){}, value:'Test participant', textContent:'', disabled:false,
    });
    return elements.get(id);
  },
};
global.location = {search:'?room=test', protocol:'https:', host:'meet.test'};
global.localStorage = {getItem(){return null}, setItem(){}};
global.window = {};
global.MediaStream = class {
  constructor(tracks=[]) { this.tracks=tracks; }
  getTracks(){return this.tracks;}
  getAudioTracks(){return this.tracks.filter(t=>t.kind==='audio');}
  getVideoTracks(){return this.tracks.filter(t=>t.kind==='video');}
};
"""
    # Initialization acquires browser devices; each test controls its own media state.
    script = script.split("// ─── Init")[0]
    result = subprocess.run(
        [node, "-"],
        input=setup + script + "\n(async () => {\n" + checks + "\n})();",
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_room_script_syntax(room_script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for browser-script regression checks")
    result = subprocess.run(
        [node, "--check"],
        input=room_script,
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_ice_candidates_wait_for_remote_description(room_script):
    run_browser_logic(
        room_script,
        """
const added = [];
await handleIce('did:test:peer', {candidate:'early'});
peers['did:test:peer'] = {
  remoteDescription:{type:'offer'},
  async addIceCandidate(candidate){added.push(candidate);},
};
await flushIce('did:test:peer');
assert.deepEqual(added, [{candidate:'early'}]);
assert.equal(pendingIce['did:test:peer'], undefined);
await handleIce('did:test:peer', {candidate:'late'});
assert.equal(added.length, 2);
""",
    )


def test_camera_free_peers_can_share_and_restore_video(room_script):
    run_browser_logic(
        room_script,
        """
const transceivers = [];
global.RTCPeerConnection = class {
  addTransceiver(track, options) {
    const sender = {track:null, async replaceTrack(t){this.track=t;}};
    transceivers.push({receiver:{track:{kind:track}},sender,options});
  }
  getTransceivers(){return transceivers;}
};
localStream = new MediaStream();
createPC('did:test:peer');
assert.equal(transceivers.length, 2);
assert.ok(transceivers.every(t=>t.options.direction==='sendrecv'));
const screen = {kind:'video'};
await replaceVideo(screen);
assert.equal(transceivers[1].sender.track, screen);
await replaceVideo(undefined);
assert.equal(transceivers[1].sender.track, null);
""",
    )


def test_new_peer_receives_active_screen_track(room_script):
    run_browser_logic(
        room_script,
        """
const sent = [];
global.RTCPeerConnection = class {
  addTransceiver(track, options){sent.push({track,options});}
};
const camera = {kind:'video'}, screen = {kind:'video'}, audio = {kind:'audio'};
localStream = new MediaStream([camera,audio]);
screenStream = new MediaStream([screen]); screenOn = true;
createPC('did:test:new');
assert.equal(sent[0].track, audio);
assert.equal(sent[1].track, screen);
""",
    )


def test_muting_stops_speech_recognition(room_script):
    run_browser_logic(
        room_script,
        """
syncMediaControls = () => {};
const track = {kind:'audio',enabled:true};
localStream = new MediaStream([track]);
let aborted = false;
recognition = {abort(){aborted=true;}};
micOn = true;
toggleMic();
assert.equal(track.enabled, false);
assert.equal(micOn, false);
assert.equal(recognition, null);
assert.equal(aborted, true);
""",
    )


def test_recording_audio_is_deduplicated_and_released(room_script):
    run_browser_logic(
        room_script,
        """
let connected = 0, disconnected = 0, stopped = 0;
audioContext = {
  createMediaStreamSource(){return {
    connect(){connected++;},disconnect(){disconnected++;}
  };},
  close(){return Promise.resolve();},
};
audioDestination = {stream:new MediaStream([{kind:'audio',stop(){stopped++;}}])};
const stream = new MediaStream([{kind:'audio',readyState:'live'}]);
addRecordingAudio(stream); addRecordingAudio(stream);
assert.equal(connected, 1);
recordingStream = new MediaStream([{kind:'video',stop(){stopped++;}}]);
releaseRecording();
assert.equal(disconnected, 1);
assert.equal(stopped, 2);
assert.equal(audioSources.size, 0);
assert.equal(audioContext, null);
""",
    )


def test_hand_raise_requires_admission(room_script):
    run_browser_logic(
        room_script,
        """
updateGrid = () => {};
const messages = [];
wsSend = msg => messages.push(msg);
toggleHand();
assert.equal(raised, false);
assert.equal(messages.length, 0);
joined = true;
toggleHand();
assert.equal(raised, true);
assert.deepEqual(messages, [{type:'hand-state',raised:true}]);
""",
    )
