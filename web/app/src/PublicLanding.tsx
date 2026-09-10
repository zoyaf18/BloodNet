import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import L, { Map as LeafletMap } from "leaflet";
import "leaflet/dist/leaflet.css";
import { api } from "./apiClient";

type Status = "GOOD" | "MODERATE" | "LOW" | "CRITICAL";

type Region = {
  id: string;
  name: string;
  state: string;
  lat: number;
  lng: number;
  zoom: number;
  status: Status;
  availability_percent: number;
  units: number;
  banks: number;
  hospitals: number;
  active_requests: number;
  eligible_donors: number;
  critical_shortages: number;
  fulfilled: number;
  data_mode: "live" | "unavailable";
  zone?: string;
};

type Dashboard = {
  selected_region: string;
  selected_region_name: string;
  data_mode: "live" | "unavailable";
  data_note: string;
  generated_at: string;
  regions: Region[];
  cities: Region[];
  overview: {
    total_units: number;
    hospitals: number;
    blood_banks: number;
    eligible_donors: number;
    active_requests: number;
    critical_shortages: number;
    fulfilled: number;
  };
  availability: Array<{
    type: string;
    available_units: number;
    required_units: number;
    availability_percent: number;
    status: Status;
  }>;
  forecast: Array<{ date: string; demand: number; supply: number }>;
  alerts: Array<{ severity: string; title: string; message: string }>;
  activity: Array<{ message: string; region: string; at: string }>;
  insight: { blood_group: string; risk: Status; message: string };
  impact: {
    requests_coordinated: number;
    units_available: number;
    requests_fulfilled: number;
    shortages_detected: number;
  };
};

type Scenario = {
  id: string;
  title: string;
  region: string;
  blood_group: string;
  units: number;
  urgency: string;
};

type ScenarioRun = {
  scenario: Scenario;
  simulation: true;
  steps: Array<{ title: string; detail: string }>;
  outcome: {
    secured_units: number;
    inventory_units: number;
    donor_units: number;
    estimated_minutes: number;
    confidence: string;
  };
};

const statusColor: Record<Status, string> = {
  GOOD: "#27866c",
  MODERATE: "#e2ac3d",
  LOW: "#e6873c",
  CRITICAL: "#df5b4b",
};

function _statusFromPercent(percent: number): Status {
  if (percent >= 70) return "GOOD";
  if (percent >= 40) return "MODERATE";
  if (percent >= 20) return "LOW";
  return "CRITICAL";
}

const emptyDashboard: Dashboard = {
  selected_region: "all",
  selected_region_name: "All India network",
  data_mode: "unavailable",
  data_note: "Loading regional intelligence…",
  generated_at: "",
  regions: [],
  cities: [],
  overview: {
    total_units: 0,
    hospitals: 0,
    blood_banks: 0,
    eligible_donors: 0,
    active_requests: 0,
    critical_shortages: 0,
    fulfilled: 0,
  },
  availability: [],
  forecast: [],
  alerts: [],
  activity: [],
  insight: { blood_group: "—", risk: "GOOD", message: "Loading forecast insight…" },
  impact: { requests_coordinated: 0, units_available: 0, requests_fulfilled: 0, shortages_detected: 0 },
};

const fallbackRegions: Region[] = [
  { id: "north", name: "North", state: "India region", lat: 28.1, lng: 77.2, zoom: 5, status: "CRITICAL", availability_percent: 0, units: 0, banks: 0, hospitals: 0, active_requests: 0, eligible_donors: 0, critical_shortages: 0, fulfilled: 0, data_mode: "unavailable" },
  { id: "south", name: "South", state: "India region", lat: 13.2, lng: 78.4, zoom: 5, status: "CRITICAL", availability_percent: 0, units: 0, banks: 0, hospitals: 0, active_requests: 0, eligible_donors: 0, critical_shortages: 0, fulfilled: 0, data_mode: "unavailable" },
  { id: "east", name: "East", state: "India region", lat: 23.2, lng: 87.4, zoom: 5, status: "CRITICAL", availability_percent: 0, units: 0, banks: 0, hospitals: 0, active_requests: 0, eligible_donors: 0, critical_shortages: 0, fulfilled: 0, data_mode: "unavailable" },
  { id: "west", name: "West", state: "India region", lat: 21.0, lng: 73.2, zoom: 5, status: "CRITICAL", availability_percent: 0, units: 0, banks: 0, hospitals: 0, active_requests: 0, eligible_donors: 0, critical_shortages: 0, fulfilled: 0, data_mode: "unavailable" },
  { id: "central", name: "Central", state: "India region", lat: 23.5, lng: 79.4, zoom: 5, status: "CRITICAL", availability_percent: 0, units: 0, banks: 0, hospitals: 0, active_requests: 0, eligible_donors: 0, critical_shortages: 0, fulfilled: 0, data_mode: "unavailable" },
];

const cityLocations = [
  ["delhi", "Delhi", "north", 28.6139, 77.2090], ["jaipur", "Jaipur", "north", 26.9124, 75.7873], ["lucknow", "Lucknow", "north", 26.8467, 80.9462], ["chandigarh", "Chandigarh", "north", 30.7333, 76.7794], ["dehradun", "Dehradun", "north", 30.3165, 78.0322],
  ["bengaluru", "Bengaluru", "south", 12.9716, 77.5946], ["chennai", "Chennai", "south", 13.0827, 80.2707], ["hyderabad", "Hyderabad", "south", 17.385, 78.4867], ["kochi", "Kochi", "south", 9.9312, 76.2673], ["coimbatore", "Coimbatore", "south", 11.0168, 76.9558],
  ["kolkata", "Kolkata", "east", 22.5726, 88.3639], ["bhubaneswar", "Bhubaneswar", "east", 20.2961, 85.8245], ["guwahati", "Guwahati", "east", 26.1445, 91.7362], ["ranchi", "Ranchi", "east", 23.3441, 85.3096], ["patna", "Patna", "east", 25.5941, 85.1376],
  ["mumbai", "Mumbai", "west", 19.076, 72.8777], ["pune", "Pune", "west", 18.5204, 73.8567], ["ahmedabad", "Ahmedabad", "west", 23.0225, 72.5714], ["surat", "Surat", "west", 21.1702, 72.8311], ["goa", "Goa", "west", 15.2993, 74.124],
  ["bhopal", "Bhopal", "central", 23.2599, 77.4126], ["indore", "Indore", "central", 22.7196, 75.8577], ["nagpur", "Nagpur", "central", 21.1458, 79.0882], ["raipur", "Raipur", "central", 21.2514, 81.6296], ["varanasi", "Varanasi", "central", 25.3176, 82.9739],
] as const;
const liveFallbackCityByZone: Record<string, string> = { north: "delhi", south: "chennai", east: "guwahati", west: "surat", central: "varanasi" };
const fallbackCities: Region[] = cityLocations.map(([id, name, zone, lat, lng]) => {
  const region = fallbackRegions.find((item) => item.id === zone)!;
  const hasData = liveFallbackCityByZone[zone] === id;
  return { ...region, id, name, zone, state: `${region.name} zone`, lat, lng, zoom: 9, units: hasData ? region.units : 0, banks: hasData ? region.banks : 0, hospitals: hasData ? region.hospitals : 0, active_requests: hasData ? region.active_requests : 0, eligible_donors: hasData ? region.eligible_donors : 0, fulfilled: hasData ? region.fulfilled : 0, status: hasData ? region.status : "CRITICAL", availability_percent: hasData ? region.availability_percent : 0 };
});

function isDashboard(value: Dashboard) {
  return Boolean(value && Array.isArray(value.regions) && value.regions.length && Array.isArray(value.availability) && value.availability.length && value.overview && Array.isArray(value.forecast));
}

function IndiaMap({
  regions, focus,
  selected,
  onSelect,
}: {
  regions: Region[];
  focus?: Region;
  selected: string;
  onSelect: (region: string) => void;
}) {
  const elementRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const markersRef = useRef<L.Marker[]>([]);

  useEffect(() => {
    if (!elementRef.current || mapRef.current) return;
    const map = L.map(elementRef.current, {
      center: [22.6, 79.2],
      zoom: 4,
      minZoom: 4,
      maxZoom: 13,
      zoomControl: false,
      attributionControl: true,
      maxBounds: L.latLngBounds([5.5, 66.2], [37.8, 99.2]),
      maxBoundsViscosity: 0.8,
    });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(map);
    L.control.zoom({ position: "topright" }).addTo(map);
    mapRef.current = map;
    window.setTimeout(() => map.invalidateSize(), 0);
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    markersRef.current.forEach((marker) => marker.remove());
    const zoneMax = regions.reduce<Record<string, number>>((max, item) => {
      const zone = item.zone || item.id;
      max[zone] = Math.max(max[zone] || 0, item.units);
      return max;
    }, {});
    markersRef.current = regions.map((region) => {
      const hasRecords = region.units + region.banks + region.hospitals + region.active_requests + region.eligible_donors > 0;
      const zone = region.zone || region.id;
      const visualPercent = region.critical_shortages >= 3
        ? 16
        : hasRecords ? Math.round(35 + 65 * region.units / Math.max(1, zoneMax[zone])) : 0;
      const color = hasRecords ? statusColor[_statusFromPercent(visualPercent)] : "#9aa0a6";
      const activeClass = selected === region.id ? " active" : "";
      const marker = L.marker([region.lat, region.lng], {
        title: `${region.name}: ${region.status}`,
        icon: L.divIcon({
          className: "bloodnet-map-marker-wrap",
          html: `<span class="bloodnet-map-marker${activeClass}" style="--marker-color:${color}"><i></i><b>${region.name}</b></span>`,
          iconSize: [96, 44],
          iconAnchor: [48, 22],
        }),
      })
        .addTo(map)
        .bindTooltip(
          `<strong>${region.name}</strong><br>${hasRecords ? `${region.units.toLocaleString("en-IN")} units · ${_statusFromPercent(visualPercent).toLowerCase()}` : "No current records"}`,
          { direction: "top", offset: [0, -16] },
        );
      marker.on("click", () => onSelect(region.id));
      return marker;
    });
  }, [regions, selected, onSelect]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const region = regions.find((item) => item.id === selected) || focus;
    if (region) map.flyTo([region.lat, region.lng], region.zoom, { duration: 0.85 });
    else map.flyTo([22.6, 79.2], 4, { duration: 0.85 });
  }, [selected, regions, focus]);

  return <div ref={elementRef} className="public-map" aria-label="Interactive India blood availability map" />;
}

function formatRelativeTime(value: string) {
  if (!value) return "just now";
  const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} hr ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}

function Droplet({ small = false }: { small?: boolean }) {
  return (
    <span className={small ? "drop-logo small" : "drop-logo"} aria-hidden="true">
      <svg viewBox="0 0 40 48"><path d="M20 2C14 11 5 21 5 31a15 15 0 0 0 30 0C35 21 26 11 20 2Z" /></svg>
    </span>
  );
}

export function PublicLanding({ auth }: { auth: ReactNode }) {
  const [selected, setSelected] = useState("all");
  const [selectedCity, setSelectedCity] = useState("");
  const [dashboard, setDashboard] = useState<Dashboard>(emptyDashboard);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [scenarioId, setScenarioId] = useState("");
  const [run, setRun] = useState<ScenarioRun | null>(null);
  const [running, setRunning] = useState(false);
  const activeSelection = selectedCity || selected;

  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    setDashboard(emptyDashboard);
    const scenarioRegion = activeSelection === "all" ? "all" : activeSelection;
    setScenarios([]);
    setScenarioId("");
    setRun(null);
    api<Dashboard>(`/match-svc/api/v1/public/dashboard?region=${encodeURIComponent(activeSelection)}`)
      .then((value) => {
        if (!isDashboard(value)) throw new Error("The dashboard API returned an incomplete response");
        if (current) setDashboard(value);
      })
      .catch(() => current && setError("Live network data is unavailable."))
      .finally(() => current && setLoading(false));
    api<{ scenarios: Scenario[] }>(`/match-svc/api/v1/public/demo-scenarios?region=${encodeURIComponent(scenarioRegion)}`)
      .then((value) => {
        if (!current || !Array.isArray(value.scenarios) || !value.scenarios.length) return;
        setScenarios(value.scenarios);
        setScenarioId(value.scenarios[0]?.id || "");
        setRun(null);
      })
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [activeSelection]);

  const selectedMapRegion = dashboard.regions.find((region) => region.id === selected);
  const selectedMapCity = dashboard.cities?.find((city) => city.id === selectedCity);
  const selectedMapPlace = selectedMapCity || selectedMapRegion;
  const selectedScenario = scenarios.find((scenario) => scenario.id === scenarioId);
  const maxForecast = Math.max(1, ...dashboard.forecast.flatMap((point) => [point.demand, point.supply]));
  const kpis = useMemo(
    () => [
      ["Blood units available", dashboard.overview.total_units.toLocaleString("en-IN"), "Across compatible inventory"],
      ["Hospitals + blood banks", (dashboard.overview.hospitals + dashboard.overview.blood_banks).toLocaleString("en-IN"), `${dashboard.overview.blood_banks} blood banks connected`],
      ["Eligible donors", dashboard.overview.eligible_donors.toLocaleString("en-IN"), "Registered eligible donor count"],
      ["Active requests", dashboard.overview.active_requests.toLocaleString("en-IN"), `${dashboard.overview.critical_shortages} need attention`],
      ["Fulfilled cases", dashboard.overview.fulfilled.toLocaleString("en-IN"), "Recorded network outcomes"],
    ],
    [dashboard],
  );

  const runScenario = async () => {
    if (!scenarioId) return;
    setRunning(true);
    setRun(null);
    const scenarioRegion = activeSelection === "all" ? "all" : activeSelection;
    try {
      const value = await api<ScenarioRun>(
        `/match-svc/api/v1/public/demo-scenarios/${encodeURIComponent(scenarioId)}/run?region=${encodeURIComponent(scenarioRegion)}`,
        { method: "POST" },
      );
      window.setTimeout(() => {
        setRun(value);
        setRunning(false);
      }, 650);
    } catch {
      setError("The demo service is unavailable.");
      setRunning(false);
    }
  };

  return (
    <div className="public-landing">
      <header className="public-nav">
        <a className="public-brand" href="#top" aria-label="BloodNet home"><Droplet small /><strong>BloodNet</strong><span>Regional intelligence</span></a>
        <nav aria-label="Public navigation">
          <a href="#overview">Live overview</a>
          <a href="#how-it-works">How it works</a>
          <a href="#demo">Try demo</a>
        </nav>
        <a className="public-login-button" href="#sign-in">Log in</a>
      </header>

      <main id="top">
        <section className="public-hero">
          <div className="hero-copy">
            <div className="hero-kicker"><span></span>Decision support for India's blood network</div>
            <h1>The right blood.<br /><em>Where it’s needed.</em><br />Before it’s urgent.</h1>
            <p>BloodNet connects demand, regional inventory, eligible donors, and fulfillment progress in one coordinated operating loop.</p>
            <div className="hero-actions">
              <a href="#overview" className="public-button primary-action">Explore the live network <span>↓</span></a>
              <a href="#demo" className="public-button secondary-action">Run a safe demo</a>
            </div>
            <div className="join-options">
              <span>New to BloodNet? Register as</span>
              <a href="#sign-in" onClick={() => window.dispatchEvent(new CustomEvent("bloodnet:register", { detail: "donor" }))}>Donor</a>
              <a href="#sign-in" onClick={() => window.dispatchEvent(new CustomEvent("bloodnet:register", { detail: "hospital_coordinator" }))}>Hospital</a>
              <a href="#sign-in" onClick={() => window.dispatchEvent(new CustomEvent("bloodnet:register", { detail: "bank_admin" }))}>Blood bank</a>
            </div>
          </div>
          <div className="hero-auth" id="sign-in">{auth}</div>
        </section>

        <section className="public-section overview-section" id="overview">
          <div className="section-heading-public">
            <div><p className="public-eyebrow">Regional overview</p><h2>India’s supply picture,<br />in one place.</h2></div>
            <div className="region-control">
              <label htmlFor="public-region">Operational region</label>
              <select id="public-region" value={selected} onChange={(event) => { setSelectedCity(""); setSelected(event.target.value); }}>
                <option value="all">All India network</option>
                {dashboard.regions.map((region) => <option key={region.id} value={region.id}>{region.name}</option>)}
              </select>
            </div>
          </div>
          <div className={`data-provenance ${dashboard.data_mode}`} role="status">
            <span>{dashboard.data_mode === "live" ? "Live network" : "Live data unavailable"}</span>
            <p>{dashboard.data_note}</p>
            {loading && <i>Refreshing…</i>}
          </div>
          {error && <div className="public-connection-note"><i></i>{error}</div>}
          <div className="public-kpis">
            {kpis.map(([label, value, note], index) => (
              <article className={index === 3 && dashboard.overview.critical_shortages ? "attention" : ""} key={label}>
                <span>{label}</span><strong>{loading ? "—" : value}</strong><small>{note}</small>
              </article>
            ))}
          </div>
        </section>

        <section className="public-section map-section">
          <div className="map-copy">
            <p className="public-eyebrow">Regional availability map</p>
            <h2>Zoom from India<br />to each network.</h2>
            <p>Choose any operational region to bring its inventory, demand, shortage risk, forecasts, and activity into focus.</p>
            <div className="map-legend">
              {Object.entries(statusColor).map(([status, color]) => <span key={status}><i style={{ background: color }}></i>{status.toLowerCase()}</span>)}
            </div>
            <div className="map-region-list" aria-label="Map region availability">
              {dashboard.regions.filter(region => ["north", "south", "east", "west", "central"].includes(region.id)).map((region) => (
                <button type="button" className={selected === region.id && !selectedCity ? "active" : ""} onClick={() => { setSelectedCity(""); setSelected(region.id); }} key={region.id}>
                  <i style={{ background: statusColor[region.status] }}></i>
                  <span><b>{region.name}</b><small>{region.units.toLocaleString("en-IN")} units · {region.active_requests} requests</small></span>
                  <strong>{region.availability_percent}%</strong>
                </button>
              ))}
            </div>
            {selectedMapPlace ? (
              <div className="region-detail-card">
                <button type="button" onClick={() => { setSelectedCity(""); setSelected("all"); }}>← All India</button>
                <div><span>{selectedMapCity ? `City · ${selectedMapPlace.state}` : `Zone · ${selectedMapPlace.state}`}</span><h3>{selectedMapPlace.name}</h3></div>
                {selectedMapPlace.units + selectedMapPlace.banks + selectedMapPlace.hospitals + selectedMapPlace.active_requests + selectedMapPlace.eligible_donors > 0
                  ? <b className={`status-pill ${selectedMapPlace.status.toLowerCase()}`}>{selectedMapPlace.status}</b>
                  : <b className="status-pill no-data">NO CURRENT RECORDS</b>}
                <dl>
                  <div><dt>Units</dt><dd>{selectedMapPlace.units.toLocaleString("en-IN")}</dd></div>
                  <div><dt>Banks</dt><dd>{selectedMapPlace.banks}</dd></div>
                  <div><dt>Hospitals</dt><dd>{selectedMapPlace.hospitals}</dd></div>
                  <div><dt>Requests</dt><dd>{selectedMapPlace.active_requests.toLocaleString("en-IN")}</dd></div>
                  <div><dt>Eligible donors</dt><dd>{selectedMapPlace.eligible_donors.toLocaleString("en-IN")}</dd></div>
                </dl>
              </div>
            ) : <p className="map-instruction">Choose a zone here or select a city marker on the map.</p>}
          </div>
          <div className="map-frame">
            <div className="map-frame-top"><span>India / {selectedMapPlace?.name || "All cities"}</span><small>Select a city · scroll or use + / − to zoom</small></div>
            <IndiaMap regions={dashboard.cities?.length ? dashboard.cities : fallbackCities} selected={selectedCity} focus={selectedMapRegion} onSelect={setSelectedCity} />
          </div>
        </section>

        <section className="public-section availability-section">
          <div className="availability-card">
            <div className="panel-title-public"><div><p className="public-eyebrow">Blood availability</p><h2>Supply against projected need.</h2></div><span>{dashboard.selected_region_name}</span></div>
            <div className="blood-grid-public">
              {dashboard.availability.map((blood) => (
                <div className="blood-row-public" key={blood.type} title={`${blood.available_units} available / ${blood.required_units} projected units`}>
                  <span className="blood-type-public">{blood.type}</span>
                  <div><i style={{ width: `${blood.availability_percent}%`, background: statusColor[blood.status] }}></i></div>
                  <strong>{blood.availability_percent}%</strong>
                  <small className={blood.status.toLowerCase()}>{blood.status}</small>
                </div>
              ))}
            </div>
          </div>
          <aside className={`forecast-insight ${dashboard.insight.risk.toLowerCase()}`}>
            <Droplet />
            <p className="public-eyebrow">Forecast insight</p>
            <h3>{dashboard.insight.blood_group} needs the closest watch.</h3>
            <p>{dashboard.insight.message}</p>
            <span>Risk: {dashboard.insight.risk.toLowerCase()}</span>
          </aside>
        </section>


        <section className="demo-band" id="demo">
          <div className="public-section demo-inner">
            <div className="demo-heading"><p className="public-eyebrow">Interactive visual demo</p><h2>See BloodNet in action.</h2><p>Choose a sample request and watch how BloodNet explores inventory, checks compatibility, and prepares a fulfillment plan for demonstration purposes.</p></div>
            <div className="scenario-picker">
              {scenarios.map((scenario) => (
                <button type="button" className={scenario.id === scenarioId ? "active" : ""} onClick={() => { setScenarioId(scenario.id); setRun(null); }} key={scenario.id}>
                  <span>{scenario.title}</span><strong>{scenario.blood_group}</strong><small>{scenario.region} · {scenario.units} units · {scenario.urgency}</small>
                </button>
              ))}
            </div>
            <div className="demo-console">
              <div className="console-request">
                <p>Sample request</p><h3>{selectedScenario?.blood_group || "—"} <span>{selectedScenario?.units || 0} units</span></h3>
                <dl><div><dt>Location</dt><dd>{selectedScenario?.region || "—"}</dd></div><div><dt>Urgency</dt><dd>{selectedScenario?.urgency || "—"}</dd></div></dl>
                <button type="button" onClick={() => void runScenario()} disabled={running || !scenarioId}>{running ? "Running decision loop…" : "Run BloodNet →"}</button>
                <small>Simulation only · no production action</small>
              </div>
              <div className={`console-steps ${running ? "running" : ""}`}>
                {!run && !running && <div className="console-empty"><Droplet /><strong>Ready when you are.</strong><span>The recommendation trail will appear here.</span></div>}
                {running && <div className="console-empty"><div className="pulse-rings"></div><strong>Evaluating regional options…</strong><span>Inventory → compatibility → eligible donor pool</span></div>}
                {run && <div className="step-results">{run.steps.map((step, index) => <article key={step.title}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{step.title}</strong><p>{step.detail}</p></div><i>✓</i></article>)}</div>}
              </div>
              {run && <div className="console-outcome"><span>Sample recommendation</span><h3>{run.outcome.secured_units}/{run.scenario.units} units secured</h3><p>{run.outcome.inventory_units} inventory + {run.outcome.donor_units} donor · ~{run.outcome.estimated_minutes} min</p><b>Visual demo complete</b></div>}
            </div>
          </div>
        </section>

        <section className="public-section how-section" id="how-it-works">
          <div className="section-heading-public"><div><p className="public-eyebrow">How BloodNet works</p><h2>One connected journey,<br />from request to response.</h2></div><p>BloodNet brings demand, forecasting, compatible supply, donor matching, and fulfillment progress into one clear regional workflow.</p></div>
          <div className="how-steps-public">
            {[["01", "Request", "A hospital shares its blood requirement.", "✚"], ["02", "Forecast", "Regional demand and supply trends are evaluated.", "↗"], ["03", "Find supply", "Compatible blood-bank inventory is checked first.", "◎"], ["04", "Match donors", "Eligible donors are considered for any remaining need.", "◎"], ["05", "Build a plan", "The strongest fulfillment route is prepared.", "✦"], ["06", "Coordinate", "Hospitals, blood banks, and donors receive the next steps.", "⇢"], ["07", "Track outcome", "Progress updates improve regional visibility.", "↻"]].map(([number, title, text, icon]) => <article key={number}><div className="how-icon">{icon}</div><span>{number}</span><h3>{title}</h3><p>{text}</p></article>)}
          </div>
        </section>

        <section className="public-section impact-section">
          <div><p className="public-eyebrow">Network impact</p><h2>Every outcome improves<br />the next response.</h2></div>
          <div className="impact-grid">
            <article><strong>{dashboard.impact.requests_coordinated.toLocaleString("en-IN")}</strong><span>Requests coordinated</span></article>
            <article><strong>{dashboard.impact.units_available.toLocaleString("en-IN")}</strong><span>Units visible</span></article>
            <article><strong>{dashboard.impact.requests_fulfilled.toLocaleString("en-IN")}</strong><span>Requests fulfilled</span></article>
            <article><strong>{dashboard.impact.shortages_detected}</strong><span>Supply risks detected</span></article>
          </div>
        </section>

        <section className="public-cta">
          <Droplet /><p className="public-eyebrow">The public view is read-only</p><h2>Ready to coordinate<br />the real network?</h2><p>Sign in for request intake, matching, approvals, donor mobilization, fulfillment, and audit.</p><a href="#sign-in" className="public-button primary-action">Log in to BloodNet ↑</a>
        </section>
      </main>

      <footer className="public-footer"><a className="public-brand" href="#top"><Droplet small /><strong>BloodNet</strong></a><p>Regional blood supply orchestration · Built for responsible human decisions.</p><span>Public dashboard · {dashboard.data_mode}</span></footer>
    </div>
  );
}
