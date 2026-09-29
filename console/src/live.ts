import { useEffect, useRef, useState } from "react";
import { type ActiveCall, type EventRow, wsUrl } from "./api";

type LiveEvent = Omit<EventRow, "id"> & { id: string };

// Subscribes to /api/live: active calls + a rolling buffer of events (optionally for one call).
export function useLive(callId?: string, max = 500) {
  const [active, setActive] = useState<ActiveCall[]>([]);
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const paused = useRef(false);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry: number | undefined;
    let closed = false;
    const connect = () => {
      ws = new WebSocket(wsUrl("/api/live", { call_id: callId }));
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = window.setTimeout(connect, 2000);
      };
      ws.onmessage = m => {
        const msg = JSON.parse(m.data);
        if (msg.kind === "active") setActive(msg.calls);
        else if (msg.kind === "event" && !paused.current) {
          setEvents(prev => [msg.event as LiveEvent, ...prev].slice(0, max));
        }
      };
    };
    setEvents([]);
    connect();
    return () => { closed = true; window.clearTimeout(retry); ws?.close(); };
  }, [callId, max]);

  return { active, events, connected, setPaused: (p: boolean) => { paused.current = p; } };
}
