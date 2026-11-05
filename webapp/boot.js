// Browser-only Tribemap (proof of concept): load Pyodide, unpack the tribemap package, start its FastAPI app
// in the page, and answer the page's fetch("/api/...") calls with it. Nothing is sent to any server.
(() => {
  const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.2/full/";
  const clan = new URLSearchParams(location.search).get("clan");   // else the one saved before
  const timings = (window.tribemapTimings = { requests: [] });
  const t0 = performance.now();
  let pyodide = null;

  const ready = (async () => {
    await new Promise((ok, fail) => {
      const s = document.createElement("script");
      s.src = PYODIDE + "pyodide.js";
      s.onload = ok;
      s.onerror = () => fail(new Error("couldn't load Pyodide"));
      document.head.appendChild(s);
    });
    const py = await loadPyodide({ indexURL: PYODIDE });
    timings.pyodide = performance.now() - t0;
    await py.loadPackage(["micropip", "sqlite3", "ssl", "pydantic"]);
    await py.pyimport("micropip").install(["fastapi==0.115.6", "starlette==0.41.3", "anyio==4.8.0", "sniffio==1.3.1",
                                             "openpyxl==3.1.5"]);
    timings.packages = performance.now() - t0;
    // the data folder lives in the browser's IndexedDB: loaded now, saved after every change (persist below)
    py.FS.mkdirTree("/data");
    py.FS.mount(py.FS.filesystems.IDBFS, {}, "/data");
    await new Promise((ok, fail) => py.FS.syncfs(true, (e) => (e ? fail(e) : ok())));
    timings.loaded = performance.now() - t0;
    const zip = await (await fetch("tribemap.zip")).arrayBuffer();
    py.unpackArchive(zip, "zip", { extractDir: "/app" });
    py.FS.writeFile("/app/webboot.py", await (await fetch("webboot.py")).text());
    py.runPython("import sys; sys.path.insert(0, '/app')");
    const boot = py.pyimport("webboot");
    boot.start(clan);
    pyodide = py;
    timings.ready = performance.now() - t0;
    return boot;
  })();
  window.tribemapReady = ready;

  // Save /data to IndexedDB, one save at a time (overlapping syncs clash). Asks the browser not to evict it.
  let saving = Promise.resolve();
  const persist = (py) => (saving = saving.then(() => new Promise((ok) => py.FS.syncfs(false, (e) => {
    if (e) console.warn("couldn't save the data to the browser", e);
    ok();
  }))));
  if (navigator.storage && navigator.storage.persist) navigator.storage.persist();

  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    const api = url.pathname.indexOf("/api/");
    if (url.origin !== location.origin || api < 0) return realFetch(input, init);
    const boot = await ready;
    const method = (init.method || "GET").toUpperCase();
    const headers = Object.entries(init.headers || {});
    const body = init.body == null ? new Uint8Array()
      : typeof init.body === "string" ? new TextEncoder().encode(init.body)
      : new Uint8Array(await new Response(init.body).arrayBuffer());
    const res = await boot.handle(method, url.pathname.slice(api), url.search.slice(1), headers, body);
    const [status, hdrs, data, ms] = res.toJs();
    res.destroy();
    if (method !== "GET" && method !== "HEAD") await persist(pyodide);
    timings.requests.push({ method, path: url.pathname.slice(api), status, ms: Math.round(ms) });
    return new Response(status === 204 ? null : data, { status, headers: hdrs });
  };
})();
