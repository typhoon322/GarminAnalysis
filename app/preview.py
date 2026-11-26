"""Server-rendered report text. Charts are added in the browser."""

from __future__ import annotations

from app.analysis import RANGE_LABELS, fmt_acwr, fmt_num


def esc(value) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def metric_domains(summary: dict, range_label: str | None = None) -> list[dict]:
    """Group headline metrics by coaching domain for the Data tab."""
    garmin = summary.get("garmin") or {}
    label = range_label or "所选范围"
    load = [
        {"label": "CTL 体能", "value": fmt_num(summary.get("ctl")), "hint": "42 天指数平均"},
        {"label": "ATL 疲劳", "value": fmt_num(summary.get("atl")), "hint": "7 天指数平均"},
        {"label": "TSB 状态", "value": fmt_num(summary.get("tsb")), "hint": "前一天 CTL − ATL"},
        {"label": "ACWR", "value": fmt_acwr(summary.get("acwr")), "hint": "近 7 日 / 近 28 日"},
        {
            "label": f"{label}负荷",
            "value": fmt_num(summary.get("range_load")),
            "hint": "所选范围合计",
        },
    ]
    recovery = [
        {
            "label": "静息心率",
            "value": f"{fmt_num(summary.get('rhr_7d'))} bpm",
            "hint": f"基线 {fmt_num(summary.get('rhr_baseline'))}",
        },
        {
            "label": "HRV",
            "value": fmt_num(summary.get("hrv_3d")),
            "hint": f"基线 {fmt_num(summary.get('hrv_baseline'))}",
        },
        {
            "label": "睡眠",
            "value": "—" if summary.get("sleep_7d_hours") is None else f"{summary['sleep_7d_hours']:.1f} 小时",
            "hint": "—"
            if summary.get("sleep_last_hours") is None
            else f"最近一夜 {summary['sleep_last_hours']:.1f} 小时",
        },
        {"label": "Body Battery", "value": fmt_num(summary.get("body_battery")), "hint": "最近读数"},
        {"label": "压力", "value": fmt_num(summary.get("stress_7d")), "hint": "近 7 日均值"},
    ]
    volume = [
        {
            "label": f"{label}时长",
            "value": "—" if summary.get("range_hours") is None else f"{summary['range_hours']:.1f} 小时",
            "hint": "所选范围内训练",
        },
        {
            "label": f"{label}距离",
            "value": "—" if summary.get("range_km") is None else f"{summary['range_km']:.1f} 公里",
            "hint": "所选范围内距离",
        },
        {
            "label": "近 7 天时长",
            "value": "—" if summary.get("weekly_hours") is None else f"{summary['weekly_hours']:.1f} 小时",
            "hint": "固定近一周对照",
        },
        {
            "label": "近 7 天距离",
            "value": "—" if summary.get("weekly_km") is None else f"{summary['weekly_km']:.1f} 公里",
            "hint": "固定近一周对照",
        },
    ]
    if summary.get("steps_7d") is not None:
        volume.append({"label": "日均步数", "value": fmt_num(summary.get("steps_7d")), "hint": "近 7 日"})
    intensity = summary.get("intensity")
    if intensity and isinstance(intensity, dict):
        volume.append(
            {
                "label": "强度分布",
                "value": (
                    f"低 {intensity.get('low', 0) * 100:.0f}% · "
                    f"中 {intensity.get('mid', 0) * 100:.0f}% · "
                    f"高 {intensity.get('high', 0) * 100:.0f}%"
                ),
                "hint": intensity.get("method_label") or "",
            }
        )
    device = []
    if garmin.get("vo2max_running") is not None or garmin.get("vo2max_cycling") is not None:
        parts = []
        if garmin.get("vo2max_running") is not None:
            parts.append(f"跑步 {fmt_num(garmin.get('vo2max_running'))}")
        if garmin.get("vo2max_cycling") is not None:
            parts.append(f"骑行 {fmt_num(garmin.get('vo2max_cycling'))}")
        device.append({"label": "VO2max", "value": " / ".join(parts), "hint": "设备最近值"})
    if garmin.get("training_status_label"):
        device.append(
            {"label": "训练状态", "value": str(garmin.get("training_status_label")), "hint": "佳明给出的状态"}
        )
    if garmin.get("weekly_training_load") is not None:
        tunnel = ""
        if garmin.get("load_tunnel_min") is not None and garmin.get("load_tunnel_max") is not None:
            tunnel = f"区间 {fmt_num(garmin.get('load_tunnel_min'))}–{fmt_num(garmin.get('load_tunnel_max'))}"
        device.append(
            {"label": "设备周负荷", "value": fmt_num(garmin.get("weekly_training_load")), "hint": tunnel or "佳明训练负荷"}
        )
    domains = [
        {"id": "load", "title": "训练负荷", "blurb": "体能、疲劳与负荷跳变", "metrics": load},
        {"id": "recovery", "title": "恢复", "blurb": "睡眠、心率变异与静息心率", "metrics": recovery},
        {"id": "volume", "title": "量与强度", "blurb": "所选范围的时长、距离与强度", "metrics": volume},
    ]
    if device:
        domains.append({"id": "device", "title": "设备状态", "blurb": "手表侧读数", "metrics": device})
    return domains


def render_report_html(report: dict, coach: dict, *, synced_at: str | None, warnings: list[str] | None) -> str:
    summary = report.get("summary") or {}
    range_label = report.get("range_label") or RANGE_LABELS.get(report.get("range") or "90", "近 90 天")
    if report.get("sample"):
        banner = (
            '<p class="banner banner-sample"><strong>示例数据</strong>'
            "不是你的佳明记录。下面故意放了一次负荷升高、静息心率上移和几晚睡眠偏短，用来把警报和图表画出来。</p>"
        )
    else:
        when = f" 同步于 {esc(synced_at)}（北京时间）。" if synced_at else ""
        banner = f'<p class="banner banner-real"><strong>佳明数据</strong>{when}</p>'
    window = report.get("window") or {}
    if window:
        window_html = (
            f"<p class=\"window\">当前范围 {esc(range_label)}："
            f"{esc(window.get('start'))} 至 {esc(window.get('end'))}，"
            f"共 {esc(window.get('days'))} 天"
            + (
                f"（全部历史 {esc(window.get('history_days'))} 天）"
                if window.get("history_days") and window.get("history_days") != window.get("days")
                else ""
            )
            + f"。负荷口径：{esc(summary.get('load_method_label'))}。</p>"
        )
    else:
        window_html = "<p class=\"window\">还没有可计算的日期。</p>"

    domains_html = []
    for domain in metric_domains(summary, range_label):
        cards = "".join(
            "<article class=\"metric\"><span>{label}</span><strong>{value}</strong><small>{hint}</small></article>".format(
                label=esc(item["label"]),
                value=esc(item["value"]),
                hint=esc(item["hint"]),
            )
            for item in domain["metrics"]
        )
        domains_html.append(
            f'<section class="domain" data-domain="{esc(domain["id"])}">'
            f"<header class=\"domain-head\"><h3>{esc(domain['title'])}</h3>"
            f"<p>{esc(domain['blurb'])}</p></header>"
            f'<div class="metrics">{cards}</div></section>'
        )

    flags = report.get("flags") or []
    if flags:
        flag_html = "".join(
            f'<li class="flag flag-{esc(item.get("level"))}"><strong>{esc(item.get("title"))}</strong>'
            f"<span>{esc(item.get('detail'))}</span></li>"
            for item in flags
        )
    else:
        flag_html = '<li class="flag flag-info"><strong>没有触发警报</strong><span>当前这组数没有碰到下面写明的那些线。</span></li>'
    suggestions = "".join(f"<li>{esc(line)}</li>" for line in report.get("suggestions") or [])
    notes = "".join(f"<li>{esc(line)}</li>" for line in report.get("notes") or [])
    methods = "".join(
        f"<dt>{esc(item.get('title'))}</dt><dd>{esc(item.get('text'))}</dd>"
        for item in report.get("methods") or []
    )
    warning_html = ""
    if warnings:
        items = "".join(f"<li>{esc(item)}</li>" for item in warnings)
        warning_html = f'<section class="block"><h2>同步提示</h2><ul>{items}</ul></section>'
    coach_body = coach.get("text") if coach.get("available") and coach.get("text") else coach.get("reason")
    return f"""
{banner}
{window_html}
<div id="domains">{"".join(domains_html)}</div>
<section class="block">
  <h2>风险与恢复</h2>
  <ul class="flags">{flag_html}</ul>
</section>
<section class="block">
  <h2>本地建议</h2>
  <ol class="suggestions">{suggestions}</ol>
</section>
<section class="block">
  <h2>图表</h2>
  <div id="charts" class="charts"></div>
</section>
<section id="coach" class="block">
  <h2>模型建议</h2>
  <p id="coach-text">{esc(coach_body)}</p>
</section>
{warning_html}
<section class="block">
  <h2>计算说明</h2>
  <ul class="notes">{notes}</ul>
</section>
<details class="block methods">
  <summary>这些数字怎么来的</summary>
  <dl>{methods}</dl>
</details>
"""
