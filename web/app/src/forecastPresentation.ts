export type ForecastDatum = {
  target_date: string;
  blood_group: string;
  component?: string;
  shortage_probability: number;
  predicted_demand?: number;
  projected_supply?: number;
};

export const CRITICAL_SHORTAGE_THRESHOLD = 0.7;

export function forecastDates(points: ForecastDatum[], horizonDays = 7) {
  return [...new Set(points.map((point) => point.target_date))]
    .sort((left, right) => left.localeCompare(right))
    .slice(0, horizonDays);
}

export function forecastBloodGroups(points: ForecastDatum[]) {
  return [...new Set(points.map((point) => point.blood_group))];
}

/** Return the worst component-level risk for a blood group on a date. */
export function forecastCellRisk(
  points: ForecastDatum[],
  bloodGroup: string,
  targetDate: string,
) {
  const matching = points.filter(
    (point) =>
      point.blood_group === bloodGroup && point.target_date === targetDate,
  );
  if (!matching.length) return null;
  return matching.reduce(
    (highest, point) =>
      Math.max(highest, Number(point.shortage_probability) || 0),
    0,
  );
}

export function summarizeForecast(points: ForecastDatum[]) {
  const atRiskGroups = new Set<string>();
  const criticalProducts = new Set<string>();
  let peakProbability = 0;
  let projectedSupplyGap = 0;

  for (const point of points) {
    const probability = Math.max(
      0,
      Math.min(1, Number(point.shortage_probability) || 0),
    );
    peakProbability = Math.max(peakProbability, probability);
    if (probability > 0) atRiskGroups.add(point.blood_group);
    if (probability >= CRITICAL_SHORTAGE_THRESHOLD) {
      criticalProducts.add(`${point.blood_group}:${point.component || "all"}`);
    }
    projectedSupplyGap += Math.max(
      (Number(point.predicted_demand) || 0) -
        (Number(point.projected_supply) || 0),
      0,
    );
  }

  return {
    peakProbability,
    atRiskBloodGroups: atRiskGroups.size,
    criticalBloodProducts: criticalProducts.size,
    projectedSupplyGap,
  };
}
