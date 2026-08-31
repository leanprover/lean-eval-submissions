/** Add UTC calendar months while clamping dates at the end of shorter months. */
export function addCalendarMonths(timestamp: string, months: number): string {
  const date = new Date(timestamp);
  const day = date.getUTCDate();
  date.setUTCDate(1);
  date.setUTCMonth(date.getUTCMonth() + months);
  const endOfMonth = new Date(Date.UTC(
    date.getUTCFullYear(),
    date.getUTCMonth() + 1,
    0,
  )).getUTCDate();
  date.setUTCDate(Math.min(day, endOfMonth));
  return date.toISOString();
}
