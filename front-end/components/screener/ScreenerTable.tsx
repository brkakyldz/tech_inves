"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowUpDown } from "lucide-react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { CoverageBadge } from "@/components/companies/CoverageBadge";
import { formatPercentile, formatScore, scoreBandStyle } from "@/lib/scoreColor";
import { COHORT_LABEL, SCORE_BAND_LABEL } from "@/lib/labels";
import { cn } from "@/lib/utils";
import type { Cohort, ScoreBlock } from "@/lib/data/types";

type SortKey = "ticker" | "compositeScore" | "sectorPercentile";

export function ScreenerTable({ scores }: { scores: ScoreBlock[] }) {
  const [cohort, setCohort] = useState<Cohort | "all">("all");
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("compositeScore");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return scores
      .filter((s) => cohort === "all" || s.cohort === cohort)
      .filter(
        (s) =>
          q === "" ||
          s.ticker.toLowerCase().includes(q) ||
          s.companyName.toLowerCase().includes(q)
      )
      .sort((a, b) => {
        const dir = sortDir === "asc" ? 1 : -1;
        if (sortKey === "ticker") return a.ticker.localeCompare(b.ticker) * dir;
        return (a[sortKey] - b[sortKey]) * dir;
      });
  }, [scores, cohort, query, sortKey, sortDir]);

  return (
    <div>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <Input
          placeholder="Search ticker or company..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="sm:max-w-xs"
        />
        <Select
          value={cohort}
          onValueChange={(v) => setCohort(v as Cohort | "all")}
        >
          <SelectTrigger className="sm:w-64">
            <SelectValue placeholder="Select cohort" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All cohorts</SelectItem>
            <SelectItem value="A">A — {COHORT_LABEL.A}</SelectItem>
            <SelectItem value="B">B — {COHORT_LABEL.B}</SelectItem>
            <SelectItem value="C">C — {COHORT_LABEL.C}</SelectItem>
          </SelectContent>
        </Select>
        <p className="text-sm text-muted-foreground sm:ml-auto">
          {filtered.length} companies
        </p>
      </div>

      <div className="mt-4 overflow-x-auto rounded-lg bg-card ring-1 ring-foreground/10">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <SortableHead label="Ticker" active={sortKey === "ticker"} dir={sortDir} onClick={() => toggleSort("ticker")} />
              <TableHead className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Company
              </TableHead>
              <TableHead className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Cohort
              </TableHead>
              <SortableHead
                label="Score"
                active={sortKey === "compositeScore"}
                dir={sortDir}
                onClick={() => toggleSort("compositeScore")}
                align="right"
              />
              <TableHead className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Band
              </TableHead>
              <SortableHead
                label="Sector %"
                active={sortKey === "sectorPercentile"}
                dir={sortDir}
                onClick={() => toggleSort("sectorPercentile")}
                align="right"
              />
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((score) => {
              const band = scoreBandStyle(score.band);
              return (
                <TableRow key={score.ticker} className="border-border/60">
                  <TableCell className="font-mono font-medium">
                    <Link
                      href={`/companies/${score.ticker}`}
                      className="hover:underline"
                    >
                      {score.ticker}
                    </Link>
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {score.companyName}
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {score.cohort}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-2">
                      <CoverageBadge coveragePct={score.coveragePct} />
                      <span className="font-mono font-medium tabular-nums">
                        {formatScore(score.compositeScore)}
                      </span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant="outline"
                      className={cn(band.text, band.border)}
                    >
                      {SCORE_BAND_LABEL[score.band]}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm text-muted-foreground tabular-nums">
                    {formatPercentile(score.sectorPercentile)}
                  </TableCell>
                </TableRow>
              );
            })}
            {filtered.length === 0 && (
              <TableRow>
                <TableCell colSpan={6} className="py-8 text-center text-sm text-muted-foreground">
                  No matching companies found.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

function SortableHead({
  label,
  active,
  dir,
  onClick,
  align = "left",
}: {
  label: string;
  active: boolean;
  dir: "asc" | "desc";
  onClick: () => void;
  align?: "left" | "right";
}) {
  return (
    <TableHead className={align === "right" ? "text-right" : undefined}>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "inline-flex items-center gap-1 text-xs font-medium tracking-wide text-muted-foreground uppercase hover:text-foreground",
          align === "right" && "flex-row-reverse",
          active && "text-foreground"
        )}
      >
        {label}
        <ArrowUpDown
          className={cn(
            "size-3.5",
            active && dir === "asc" && "rotate-180"
          )}
        />
      </button>
    </TableHead>
  );
}
