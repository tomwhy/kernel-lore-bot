from kernel_lore_bot.progress import NullProgress


def test_null_progress_is_silent_and_chainable(capsys):
    bar = NullProgress().bar("anything", total=10)
    bar.update(3)
    bar.set_note("note")
    bar.close()
    assert capsys.readouterr().err == ""


def test_null_bar_works_as_context_manager():
    with NullProgress().bar("desc") as bar:
        bar.update()
