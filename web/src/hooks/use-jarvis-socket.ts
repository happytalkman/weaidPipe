"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getToken, WS_URL } from "@/lib/api";

export type JarvisState = "OFFLINE" | "CONNECTING" | "LISTENING" | "THINKING" | "SPEAKING" | "MUTED";
export type Message = { id: string; role: "user" | "assistant" | "system"; content: string; at: string };
export type Progress = { kind: string; label: string; percent: number; phase: string } | null;

type Capture = {
  context: AudioContext;
  stream: MediaStream;
  source: MediaStreamAudioSourceNode;
  processor: ScriptProcessorNode;
  sink: GainNode;
};

function pcm16(input: Float32Array, sourceRate: number, targetRate = 16000) {
  const ratio = sourceRate / targetRate;
  const length = Math.max(1, Math.floor(input.length / ratio));
  const output = new Int16Array(length);
  for (let i = 0; i < length; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j += 1) sum += input[j];
    const value = Math.max(-1, Math.min(1, sum / Math.max(1, end - start)));
    output[i] = value < 0 ? value * 32768 : value * 32767;
  }
  return output;
}

export function useJarvisSocket() {
  const socket = useRef<WebSocket | null>(null);
  const capture = useRef<Capture | null>(null);
  const playback = useRef<AudioContext | null>(null);
  const nextPlaybackAt = useRef(0);
  const desired = useRef(true);
  const [state, setState] = useState<JarvisState>("CONNECTING");
  const [messages, setMessages] = useState<Message[]>([]);
  const [transcript, setTranscript] = useState("");
  const [progress, setProgress] = useState<Progress>(null);
  const [error, setError] = useState("");
  const [micActive, setMicActive] = useState(false);

  const ensurePlayback = useCallback(async () => {
    if (!playback.current) playback.current = new AudioContext({ sampleRate: 24000 });
    if (playback.current.state === "suspended") await playback.current.resume();
    return playback.current;
  }, []);

  const playAudio = useCallback(async (encoded: string) => {
    const context = await ensurePlayback();
    const binary = window.atob(encoded);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    const view = new DataView(bytes.buffer);
    const samples = new Float32Array(Math.floor(bytes.byteLength / 2));
    for (let i = 0; i < samples.length; i += 1) samples[i] = view.getInt16(i * 2, true) / 32768;
    const buffer = context.createBuffer(1, samples.length, 24000);
    buffer.copyToChannel(samples, 0);
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    const start = Math.max(context.currentTime + 0.02, nextPlaybackAt.current);
    source.start(start);
    nextPlaybackAt.current = start + buffer.duration;
  }, [ensurePlayback]);

  useEffect(() => {
    desired.current = true;
    let retry: ReturnType<typeof setTimeout> | undefined;

    function connect() {
      const token = getToken();
      if (!token || !desired.current) return;
      setState("CONNECTING");
      const ws = new WebSocket(`${WS_URL}/ws?token=${encodeURIComponent(token)}`);
      ws.binaryType = "arraybuffer";
      socket.current = ws;
      ws.onmessage = (message) => {
        const event = JSON.parse(String(message.data));
        if (event.type === "status") setState(event.state as JarvisState);
        if (event.type === "ready") setError("");
        if (event.type === "message") {
          setMessages((current) => {
            const role = event.role as Message["role"];
            const previous = current[current.length - 1];
            if (previous?.role === role && previous.content === event.content) return current;
            return [...current.slice(-99), { id: crypto.randomUUID(), role, content: event.content, at: event.timestamp }];
          });
        }
        if (event.type === "transcript") setTranscript((value) => `${value} ${event.content}`.trim());
        if (event.type === "transcript_clear") setTranscript("");
        if (event.type === "audio") playAudio(event.data).catch(() => setError("Audio playback needs browser permission."));
        if (event.type === "progress") setProgress({ kind: event.kind, label: event.label, percent: event.percent, phase: event.phase });
        if (event.type === "progress_end" || event.type === "progress_hide") setProgress(null);
        if (event.type === "error") setError(event.message || "Live session error");
      };
      ws.onerror = () => setError("The live channel could not be reached.");
      ws.onclose = (event) => {
        if (socket.current === ws) socket.current = null;
        setState("OFFLINE");
        if (event.code === 4403) setError("Your Gemini key needs attention.");
        if (desired.current) retry = setTimeout(connect, 2500);
      };
    }

    connect();
    return () => {
      desired.current = false;
      if (retry) clearTimeout(retry);
      socket.current?.close();
    };
  }, [playAudio]);

  const sendText = useCallback(async (content: string) => {
    const clean = content.trim();
    if (!clean || socket.current?.readyState !== WebSocket.OPEN) return false;
    await ensurePlayback();
    setMessages((current) => [...current.slice(-99), { id: crypto.randomUUID(), role: "user", content: clean, at: new Date().toISOString() }]);
    socket.current.send(JSON.stringify({ type: "text", content: clean }));
    return true;
  }, [ensurePlayback]);

  const stopMic = useCallback(async () => {
    const current = capture.current;
    if (current) {
      current.processor.disconnect();
      current.source.disconnect();
      current.sink.disconnect();
      current.stream.getTracks().forEach((track) => track.stop());
      await current.context.close();
      capture.current = null;
    }
    setMicActive(false);
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify({ type: "mute", muted: true }));
  }, []);

  const startMic = useCallback(async () => {
    if (capture.current) return;
    try {
      await ensurePlayback();
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      const context = new AudioContext();
      const source = context.createMediaStreamSource(stream);
      const processor = context.createScriptProcessor(4096, 1, 1);
      const sink = context.createGain();
      sink.gain.value = 0;
      processor.onaudioprocess = (event) => {
        if (socket.current?.readyState !== WebSocket.OPEN) return;
        const encoded = pcm16(event.inputBuffer.getChannelData(0), context.sampleRate);
        socket.current.send(encoded.buffer);
      };
      source.connect(processor);
      processor.connect(sink);
      sink.connect(context.destination);
      capture.current = { context, stream, source, processor, sink };
      setMicActive(true);
      socket.current?.send(JSON.stringify({ type: "mute", muted: false }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Microphone access was denied.");
    }
  }, [ensurePlayback]);

  useEffect(() => () => { void stopMic(); void playback.current?.close(); }, [stopMic]);

  return { state, messages, transcript, progress, error, micActive, sendText, startMic, stopMic };
}
