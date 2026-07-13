// WebSocket client with auto-reconnect for the live traffic feed.
const LiveFeed = (() => {
  let ws;
  let listeners = [];
  let statusListeners = [];
  let retryDelay = 1000;

  function onMessage(fn) { listeners.push(fn); }
  function onStatus(fn) { statusListeners.push(fn); }
  function emitStatus(s) { statusListeners.forEach((fn) => fn(s)); }

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/live`);

    ws.onopen = () => {
      retryDelay = 1000;
      emitStatus("live");
    };
    ws.onclose = () => {
      emitStatus("down");
      setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 1.6, 15000);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (evt) => {
      const data = JSON.parse(evt.data);
      listeners.forEach((fn) => fn(data));
    };
  }

  return { connect, onMessage, onStatus };
})();
