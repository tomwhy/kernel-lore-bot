from datetime import datetime, timedelta, timezone

from kernel_lore_bot.delivery.formatting import (
    format_header,
    format_thread,
    format_update_notification,
)
from kernel_lore_bot.models import Classified, Entry, Node, Thread, ThreadStatus

CUTOFF = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


def _entry(msg_id, updated, title="A patch", author="Alice Adams", url=None):
    return Entry(
        id=msg_id,
        title=title,
        url=url or f"https://lore.kernel.org/all/{msg_id}",
        author=author,
        updated=updated,
        reply=None,
    )


def _thread(updated=None, mailing_lists=frozenset({"netdev"}), children=(), **kw):
    root = Node(entry=_entry("root@x.com", updated or CUTOFF, **kw), children=children)
    return Thread(roots=(root,), mailing_lists=frozenset(mailing_lists))


def _new(thread):
    return Classified(thread=thread, status=ThreadStatus.NEW)


def test_new_thread_layout_is_exact():
    text = format_thread(_new(_thread()), CUTOFF)
    assert text == (
        "🆕 <b>A patch</b>\n"
        "👤 Alice Adams  🕐 2026-07-16 12:00 UTC\n"
        "📬 netdev\n"
        "<i>... 1 new entry</i>\n"
        '<a href="https://lore.kernel.org/all/root@x.com">🔗 View thread</a>'
    )


def test_updated_thread_uses_the_updated_badge():
    thread = _thread(updated=CUTOFF - timedelta(hours=5))
    text = format_thread(Classified(thread=thread, status=ThreadStatus.UPDATED), CUTOFF)
    assert text.startswith("🔄 <b>A patch</b>")


def test_mailing_list_line_is_omitted_when_empty():
    text = format_thread(_new(_thread(mailing_lists=frozenset())), CUTOFF)
    assert "📬" not in text


def test_all_mailing_lists_are_shown_sorted():
    text = format_thread(_new(_thread(mailing_lists={"netdev", "lkml"})), CUTOFF)
    assert "📬 lkml, netdev" in text


def test_entry_count_line_is_omitted_when_nothing_is_new():
    thread = _thread(updated=CUTOFF - timedelta(days=1))
    text = format_thread(Classified(thread=thread, status=ThreadStatus.UPDATED), CUTOFF)
    assert "new entry" not in text and "new entries" not in text


def test_entry_count_is_pluralised():
    reply = Node(entry=_entry("r@x.com", CUTOFF + timedelta(minutes=5)))
    text = format_thread(_new(_thread(children=(reply,))), CUTOFF)
    assert "<i>... 2 new entries</i>" in text


def test_html_special_characters_in_subject_are_escaped():
    # LKML subjects legitimately contain < > &.
    text = format_thread(_new(_thread(title="[PATCH] fix <foo> & <bar>")), CUTOFF)
    assert "&lt;foo&gt; &amp; &lt;bar&gt;" in text
    assert "<foo>" not in text


def test_html_special_characters_in_author_are_escaped():
    text = format_thread(_new(_thread(author="<script>alert(1)</script>")), CUTOFF)
    assert "<script>" not in text


def test_url_is_escaped_inside_the_href_attribute():
    # DEFECT 12: Message-IDs come from an untrusted header.
    hostile = 'https://lore.kernel.org/all/x"><b>oops'
    text = format_thread(_new(_thread(url=hostile)), CUTOFF)
    assert '"><b>oops' not in text
    assert "&quot;&gt;&lt;b&gt;oops" in text


def test_update_notification_layout_is_exact():
    text = format_update_notification(_thread())
    assert text == (
        "🔔 <b>Thread update</b>\n"
        "<b>A patch</b>\n"
        "👤 Alice Adams  🕐 2026-07-16 12:00 UTC\n"
        "📬 netdev\n"
        '<a href="https://lore.kernel.org/all/root@x.com">🔗 View thread</a>'
    )


def test_update_notification_omits_empty_mailing_list():
    assert "📬" not in format_update_notification(_thread(mailing_lists=frozenset()))


def test_header_layout_is_exact():
    text = format_header(3, datetime(2026, 7, 16, 8, 30, tzinfo=timezone.utc))
    assert text == (
        "🐧 <b>Kernel Lore Digest</b>\n<i>2026-07-16 08:30 UTC</i> — <b>3</b> new thread(s)"
    )
