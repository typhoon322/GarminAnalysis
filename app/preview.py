"""Server-rendered report text. Charts are added in the browser."""

from __future__ import annotations

from app.analysis import fmt_acwr, fmt_num


def esc(value) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _cards(summary: dict) -> list[tuple[str, str, str]]:
    garmin = summary.get("garmin") or {}
    cards = [
        ("CTL 体能", fmt_num(summary.get("ctl")), "42 天指数平均"),
        ("ATL 疲劳", fmt_num(summary.get("atl")), "7 天指数平均"),
        ("TSB 状态", fmt_num(summary.get("tsb")), "前一天 CTL − ATL"),
        ("ACWR", fmt_acwr(summary.get("acwr")), "近 7 日 / 近 28 日"),
        ("近 7 天时长", "—" if summary.get("weekly_hours") is None else f"{summary['weekly_hours']:.1f} 小时", "训练时长"),
        ("近 7 天距离", "—" if summary.get("weekly_km") is None else f"{summary['weekly_km']:.1f} 公里", "活动距离"),
        (
            "静息心率",
            f"{fmt_num(summary.get('rhr_7d'))} bpm",
            f"基线 {fmt_num(summary.get('rhr_baseline'))}",
        ),
        (
            "HRV",
            fmt_num(summary.get("hrv_3d")),
            f"基线 {fmt_num(summary.get('hrv_baseline'))}",
        ),
        (
            "睡眠",
            "—" if summary.get("sleep_7d_hours") is None else f"{summary['sleep_7d_hours']:.1f} 小时",
            "—" if summary.get("sleep_last_hours") is None else f"最近一夜 {summary['sleep_last_hours']:.1f} 小时",
        ),
        ("Body Battery", fmt_num(summary.get("body_battery")), "最近读数"),
        ("压力", fmt_num(summary.get("stress_7d")), "近 7 日均值"),
    ]
    if summary.get("steps_7d") is not None:
        cards.append(("日均步数", fmt_num(summary.get("steps_7d")), "近 7 日"))
    if garmin.get("vo2max_running") is not None or garmin.get("vo2max_cycling") is not None:
        parts = []
        if garmin.get("vo2max_running") is not None:
            parts.append(f"跑步 {fmt_num(garmin.get('vo2max_running'))}")
        if garmin.get("vo2max_cycling") is not None:
            parts.append(f"骑行 {fmt_num(garmin.get('vo2max_cycling'))}")
        cards.append(("VO2max", " / ".join(parts), "设备最近值"))
    if garmin.get("training_status_label"):
        cards.append(("训练状态", str(garmin.get("training_status_label")), "佳明给出的状态"))
    if garmin.get("weekly_training_load") is not None:
        tunnel = ""
        if garmin.get("load_tunnel_min") is not None and garmin.get("load_tunnel_max") is not None:
            tunnel = f"区间 {fmt_num(garmin.get('load_tunnel_min'))}–{fmt_num(garmin.get('load_tunnel_max'))}"
        cards.append(("设备周负荷", fmt_num(garmin.get("weekly_training_load")), tunnel or "佳明训练负荷"))
    return cards


def render_report_html(report: dict, coach: dict, *, synced_at: str | None, warnings: list[str] | None) -> str:
    summary = report.get("summary") or {}
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
            f"<p class=\"window\">数据日期 {esc(window.get('start'))} 至 {esc(window.get('end'))}，"
            f"共 {esc(window.get('days'))} 天。负荷口径：{esc(summary.get('load_method_label'))}。</p>"
        )
    else:
        window_html = "<p class=\"window\">还没有可计算的日期。</p>"
    cards = "".join(
        "<article class=\"metric\"><span>{label}</span><strong>{value}</strong><small>{hint}</small></article>".format(
            label=esc(label),
            value=esc(value),
            hint=esc(hint),
        )
        for label, value, hint in _cards(summary)
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
        warning_html = f'<section class="card"><h2>同步提示</h2><ul>{items}</ul></section>'
    coach_body = coach.get("text") if coach.get("available") and coach.get("text") else coach.get("reason")
    return f"""
{banner}
{window_html}
<section class="metrics">{cards}</section>
<section class="card">
  <h2>风险与恢复</h2>
  <ul class="flags">{flag_html}</ul>
</section>
<section class="card">
  <h2>本地建议</h2>
  <ol class="suggestions">{suggestions}</ol>
</section>
<section class="card">
  <h2>图表</h2>
  <div id="charts"></div>
</section>
<section id="coach" class="card">
  <h2>模型建议</h2>
  <p id="coach-text">{esc(coach_body)}</p>
</section>
{warning_html}
<section class="card">
  <h2>计算说明</h2>
  <ul class="notes">{notes}</ul>
</section>
<details class="card methods">
  <summary>这些数字怎么来的</summary>
  <dl>{methods}</dl>
</details>
"""
