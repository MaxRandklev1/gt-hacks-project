import { useEffect, useState } from 'react';
import type { Job } from '../lib/client';
import { durationText, enrollmentDisplay } from '../lib/enrollment-display';
import { Icon } from './Icon';
import './EnrollmentProgress.css';

export function EnrollmentProgress({ job, awaitingIdentity = false }: {
  job?: Job; awaitingIdentity?: boolean;
}) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    setNow(Date.now());
    if (job?.status === 'completed' || job?.status === 'failed') return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [job?.id, job?.status]);
  const state = enrollmentDisplay(job, now, awaitingIdentity);
  const status = state.failed ? 'SETUP STOPPED' : state.completed ? 'LOOK SAVED'
    : state.queued ? 'IN THE QUEUE' : 'SETTING UP YOUR LIKENESS';
  const counter = state.counter;

  return <div className={`enrollment-progress${state.failed ? ' has-failed' : ''}`}>
    <div className="enrollment-status-card">
      <div className="enrollment-status-top"><span className="enrollment-status-label"><Icon name={state.failed ? 'retry' : state.completed ? 'check' : state.queued ? 'clock' : 'spark'} size={15} />{status}</span>
        {state.elapsed !== undefined && <span className="enrollment-elapsed" title="Time since your request was submitted"><Icon name="clock" size={13} />{durationText(state.elapsed)} elapsed</span>}
      </div>
      <div role={state.failed ? 'alert' : 'status'} aria-live="polite" aria-atomic="true">
        <h2>{state.title}</h2><p>{state.detail}</p>
        {state.message && <p className="enrollment-worker-message">{state.message}</p>}
      </div>
      {!state.failed && !state.completed && <div className="enrollment-meter">
        {counter && <div className="enrollment-counter"><span>{counter.label}</span><strong>{counter.value} of {counter.total}</strong></div>}
        <div className={`progress-track${counter ? '' : ' indeterminate'}`} role="progressbar"
          aria-label={counter?.label || state.title} aria-valuemin={0} aria-valuemax={counter?.total}
          aria-valuenow={counter?.value} aria-valuetext={counter ? `${counter.value} of ${counter.total}` : 'Progress is not yet measurable'}>
          <span style={counter ? { width: `${counter.value / counter.total * 100}%` } : undefined} />
        </div>
        {counter && <p className="enrollment-counter-note">{state.queued ? 'Shared preparation only. Your likeness setup is next.' : 'Image creation only. Detail preparation and saving follow.'}</p>}
      </div>}
      {state.updateAge !== undefined && <p className={`enrollment-update${state.stale ? ' is-delayed' : ''}`}>
        {state.stale ? <><Icon name="clock" size={14} /><span>No new update for {durationText(state.updateAge)}. Your request is still marked as {state.queued ? 'queued' : 'in progress'}. We’re waiting for the next update; this does not confirm a failure.</span></>
          : <>Last update {state.updateAge < 5_000 ? 'just now' : `${durationText(state.updateAge)} ago`}</>}
      </p>}
      {state.noWorkerUpdateYet && <p className="enrollment-update is-delayed"><Icon name="clock" size={14} /><span>No processing update received yet. Your request is saved and waiting for the worker.</span></p>}
    </div>
    {!state.failed && <ol className="enrollment-checklist" aria-label="Your likeness setup stages">
      {state.steps.map((step, index) => <li key={step.label} className={`is-${step.state}`} aria-current={step.state === 'current' ? 'step' : undefined}>
        <span className="enrollment-step-mark" aria-hidden="true">{step.state === 'done' ? <Icon name="check" size={15} /> : step.state === 'current' ? <span className="spinner" /> : String(index + 1).padStart(2, '0')}</span>
        <div><strong>{step.label}<span className="sr-only"> — {step.state === 'done' ? 'Complete' : step.state === 'current' ? 'In progress' : 'Not started'}</span></strong><p>{step.detail}</p></div>
      </li>)}
    </ol>}
  </div>;
}
