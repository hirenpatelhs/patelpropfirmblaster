"use client";

import { useEffect, useState } from "react";

export function useUtcClock(): string {
  const [time, setTime] = useState("--:--:-- UTC");
  useEffect(() => {
    const update = () => setTime(`${new Date().toISOString().slice(11, 19)} UTC`);
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, []);
  return time;
}
