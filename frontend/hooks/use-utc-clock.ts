"use client";

import { useEffect, useState } from "react";

export function useUtcClock(): string {
  const [time, setTime] = useState("--:--:-- London");
  useEffect(() => {
    const formatter = new Intl.DateTimeFormat("en-GB", {
      timeZone: "Europe/London",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
      timeZoneName: "short",
    });
    const update = () => setTime(formatter.format(new Date()));
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, []);
  return time;
}
