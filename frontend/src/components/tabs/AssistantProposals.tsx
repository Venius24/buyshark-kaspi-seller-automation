import { useRef, useState } from 'react';
import {
  AssistantProposalError,
  useApproveProposal,
  useAssistantProposals,
  useRejectProposal,
  type AssistantProposal,
  type AssistantProposalStatus,
} from '../../hooks/useAssistant';
import { useActiveStoreId, useActiveStoreRole, useHasSection } from '../../storeContext';

export function AssistantProposals() {
  const storeId = useActiveStoreId();
  const role = useActiveStoreRole();
  const hasSection = useHasSection();
  if ((role !== 'MANAGER' && role !== 'OWNER') || !hasSection('assistant')) return null;
  return <ProposalPanel key={storeId} />;
}

function ProposalPanel() {
  // Отдельный запрос pending: общий список ограничен сервером и может не
  // включать старые предложения, которые всё ещё ждут решения.
  const pending = useAssistantProposals('pending');
  const recent = useAssistantProposals();
  const approve = useApproveProposal();
  const reject = useRejectProposal();
  const [notice, setNotice] = useState<string | null>(null);
  const deciding = useRef(false);
  const errors = [pending.error, recent.error, approve.error, reject.error];
  const forbidden = errors.some((error) => error instanceof AssistantProposalError && error.status === 403);
  const busy = approve.isPending || reject.isPending;
  const history = recent.proposals
    .filter((proposal) => proposal.status !== 'pending')
    .sort((a, b) => (b.decided_at ?? b.created_at ?? '').localeCompare(a.decided_at ?? a.created_at ?? ''))
    .slice(0, 10);

  const decide = async (proposal: AssistantProposal, action: 'approve' | 'reject') => {
    if (deciding.current) return;
    deciding.current = true;
    setNotice(null);
    try {
      const result = await (action === 'approve' ? approve : reject).mutateAsync(proposal.id);
      setNotice(result.status === 'approved'
        ? 'Одобрено в пробном режиме. Цена в Kaspi не изменена.'
        : result.status === 'failed'
          ? `Ошибка пробного применения: ${resultText(result.apply_result)}`
          : `Предложение отклонено.${result.apply_result ? ` ${resultText(result.apply_result)}` : ''}`);
    } catch (error) {
      setNotice(error instanceof AssistantProposalError && error.status === 409
        ? 'Предложение уже решено другим пользователем. Список обновляется.'
        : `Не удалось сохранить решение: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      deciding.current = false;
    }
  };

  if (forbidden) return null;

  return (
    <section aria-label="Предложения цены" style={{ flex: '0 1 auto', minHeight: 0, maxHeight: '40%', overflowY: 'auto', padding: 12, borderBottom: '1px solid rgba(255,255,255,0.08)', fontSize: 13, overflowWrap: 'anywhere' }}>
      <h3 style={{ fontSize: 14, margin: '0 0 8px' }}>Предложения цены</h3>
      {notice && <div role="status" style={{ marginBottom: 8 }}>{notice}</div>}
      {(pending.error || recent.error) && (
        <div className="alert alert-danger" role="alert" style={{ marginBottom: 8 }}>
          Не удалось обновить предложения: {(pending.error || recent.error)?.message}
        </div>
      )}
      {pending.isLoading && <div style={mutedStyle}>Загрузка предложений…</div>}
      {!pending.isLoading && !pending.error && pending.proposals.length === 0 && <div style={mutedStyle}>Нет предложений, ожидающих решения.</div>}
      {[...pending.proposals].sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? '')).map((proposal) => (
        <div key={proposal.id} style={rowStyle}>
          <ProposalInfo proposal={proposal} />
          <p style={{ margin: '8px 0', color: '#e0a33e', fontSize: 12 }}>Режим: пробный — цена в Kaspi не меняется</p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            <button className="btn btn-primary" disabled={busy || !!pending.error} onClick={() => decide(proposal, 'approve')}>Одобрить</button>
            <button className="btn btn-secondary" disabled={busy || !!pending.error} onClick={() => decide(proposal, 'reject')}>Отклонить</button>
          </div>
        </div>
      ))}
      <details style={{ marginTop: 10 }}>
        <summary style={{ cursor: 'pointer' }}>Последние решённые ({history.length})</summary>
        {recent.isLoading && <div style={mutedStyle}>Загрузка истории…</div>}
        {!recent.isLoading && !recent.error && history.length === 0 && <div style={mutedStyle}>Решённых предложений пока нет.</div>}
        {history.map((proposal) => (
          <div key={proposal.id} style={rowStyle}>
            <span style={{ color: statusColors[proposal.status], fontSize: 12 }}>{statusLabels[proposal.status]}</span>
            <ProposalInfo proposal={proposal} />
            {proposal.status === 'approved' && <div style={mutedStyle}>Пробный режим: цена в Kaspi не изменена.</div>}
            {proposal.apply_result && proposal.status !== 'approved' && <div style={{ color: '#f87171', fontSize: 12 }}>{resultText(proposal.apply_result)}</div>}
            {proposal.status === 'failed' && !proposal.apply_result && <div style={{ color: '#f87171', fontSize: 12 }}>Не удалось выполнить пробное применение.</div>}
          </div>
        ))}
      </details>
    </section>
  );
}

function ProposalInfo({ proposal }: { proposal: AssistantProposal }) {
  return (
    <>
      <div><strong>SKU: {proposal.sku}</strong></div>
      <div>{price(proposal.old_price)} → {price(proposal.new_price)}</div>
      <div style={{ whiteSpace: 'pre-wrap', margin: '4px 0' }}>{proposal.reason}</div>
      <div style={mutedStyle}>Границы при создании: мин. {price(proposal.min_price_at_creation)}, макс. {price(proposal.max_price_at_creation)}</div>
      <div style={mutedStyle}>Создано: {proposal.created_at ? new Date(proposal.created_at).toLocaleString('ru-RU') : 'не указано'}</div>
    </>
  );
}

function price(value: number | null) {
  return value === null ? 'не указана' : `${value.toLocaleString('ru-RU')} ₸`;
}

function resultText(result: AssistantProposal['apply_result']) {
  if (!result) return 'Причина не указана.';
  return String(result.error ?? JSON.stringify(result)).slice(0, 240);
}

const statusLabels: Record<AssistantProposalStatus, string> = { pending: 'Ожидает решения', approved: 'Одобрено', rejected: 'Отклонено', failed: 'Ошибка' };
const statusColors: Record<AssistantProposalStatus, string> = { pending: '#e0a33e', approved: '#34d399', rejected: '#e0a33e', failed: '#f87171' };
const mutedStyle: React.CSSProperties = { fontSize: 12, opacity: 0.6 };
const rowStyle: React.CSSProperties = { padding: '8px 0', borderTop: '1px solid rgba(255,255,255,0.08)' };
