from pathlib import Path
import pytest
from scripts.run_strategy_batch import specifications


def case(**patch):
    return dict(name='run', candidate='candidate', plan='plan', baseline_run=['baseline'], **patch)


def test_batch_keeps_explicit_windows_and_delay():
    result = specifications([case(end_time='08:00:00', new_order_activation_delay_ms=250)], Path('output'), Path('runtime'))[0]
    assert result.output == Path('output/run')
    assert result.end_time == '08:00:00'
    assert result.start_time is None
    assert result.new_order_activation_delay_ms == 250


@pytest.mark.parametrize('rows', [[], [case(unknown=True)],
    [dict(case(), name='../escape')], [dict(case(), name='D:escape')],
    [case(), dict(case(), name='RUN')], [dict(case(), baseline_run='baseline')]])
def test_batch_rejects_ambiguous_or_escaping_cases(rows):
    with pytest.raises(ValueError):
        specifications(rows, Path('output'), Path('runtime'))
