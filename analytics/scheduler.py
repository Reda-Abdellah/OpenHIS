from apscheduler.schedulers.asyncio import AsyncIOScheduler

_sched = AsyncIOScheduler(timezone="UTC")


def start_scheduler(interval_min: int = 5):
    from collector import collect_and_store
    _sched.add_job(collect_and_store, 'interval', minutes=interval_min, id='collect_metrics')
    _sched.start()


def stop_scheduler():
    if _sched.running:
        _sched.shutdown(wait=False)
