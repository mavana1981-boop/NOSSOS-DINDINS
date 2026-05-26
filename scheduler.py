{% extends "base.html" %}
{% block title %}{{ project.name }}{% endblock %}
{% block content %}

<div class="page-header">
  <div class="page-title-wrap">
    <h1>{{ project.name }}</h1>
    <p>
      Meta de <strong>{{ project.target_amount|brl }}</strong>
      {% if project.deadline %} · prazo {{ project.deadline|data_br }}{% endif %}
      {% if project.is_completed %} · <span class="badge badge-done">✓ Concluído</span>{% endif %}
    </p>
  </div>
  <div class="flex flex-gap">
    {% if can_edit %}
      <a href="{{ url_for('projects.edit_project', project_id=project.id) }}" class="btn btn-ghost">Editar</a>
      <form method="POST" action="{{ url_for('projects.delete_project', project_id=project.id) }}"
            onsubmit="return confirm('Excluir o projeto?');">
        <button class="btn btn-danger">Excluir</button>
      </form>
    {% endif %}
    <a href="{{ url_for('projects.list_projects') }}" class="btn btn-ghost">← Voltar</a>
  </div>
</div>

{% if project.description %}
<div class="card mb-3">
  <p style="margin:0;font-family:'Fraunces',serif;font-style:italic;color:var(--text-dim);font-size:1.05rem;">
    "{{ project.description }}"
  </p>
</div>
{% endif %}

<!-- Progresso destaque -->
<div class="card mb-3">
  <div class="flex-between mb-2">
    <h3 style="margin:0;">Progresso</h3>
    <div style="font-family:'Fraunces',serif;font-size:1.8rem;font-weight:700;color:var(--accent);">
      {{ project.progress_percent }}%
    </div>
  </div>
  <div class="progress" style="height:14px;">
    <div class="progress-bar {{ 'complete' if project.is_completed }}" style="width: {{ project.progress_percent }}%;"></div>
  </div>
  <div class="flex-between mt-2">
    <div>
      <div class="text-faint text-small">acumulado</div>
      <div class="mono" style="font-size:1.3rem;color:var(--green);">{{ project.total_raised|brl }}</div>
    </div>
    <div class="text-right">
      <div class="text-faint text-small">faltam</div>
      <div class="mono" style="font-size:1.3rem;color:var(--text-dim);">{{ project.remaining|brl }}</div>
    </div>
  </div>
</div>

<div class="grid grid-2 mb-3">
  <!-- Membros e contribuições -->
  <div class="card">
    <div class="card-title"><h3>Membros & contribuições</h3></div>
    {% if user_totals %}
      {% for ut in user_totals|sort(attribute='total', reverse=True) %}
      <div class="flex flex-gap-lg" style="padding:12px 0;border-bottom:1px solid var(--border);">
        {% if ut.user.photo %}
          <img src="{{ ut.user.photo_url }}" class="avatar">
        {% else %}
          <div class="avatar-fallback">{{ ut.user.full_name[0]|upper }}</div>
        {% endif %}
        <div style="flex:1;">
          <strong>{{ ut.user.full_name }}</strong>
          <div class="text-faint text-small">{{ ut.count }} aporte{{ 's' if ut.count != 1 }}</div>
        </div>
        <div class="mono" style="color:var(--green);font-weight:600;">{{ ut.total|brl }}</div>
      </div>
      {% endfor %}
    {% else %}
      <p class="text-faint">Nenhuma contribuição ainda. Comece pelos aportes abaixo.</p>
    {% endif %}

    {% if project.monthly_auto and project.monthly_auto > 0 %}
    <div class="alert alert-info mt-2" style="margin-bottom:0;">
      <strong>Aporte automático ativo</strong><br>
      <span class="text-small">
        {{ project.monthly_auto|brl }} são lançados todo dia {{ project.auto_day }} do mês,
        distribuídos pelas cotas dos membros.
      </span>
    </div>
    {% endif %}
  </div>

  <!-- Form de aporte manual -->
  <div class="card">
    <div class="card-title"><h3>Novo aporte</h3></div>
    {% if not project.is_completed %}
    <form method="POST" action="{{ url_for('projects.add_contribution', project_id=project.id) }}">
      <div class="form-group">
        <label class="form-label">Valor (R$) *</label>
        <input class="form-control mono" name="amount" required inputmode="decimal" placeholder="0,00">
      </div>
      <div class="form-group">
        <label class="form-label">Data</label>
        <input class="form-control" type="date" name="contributed_at">
      </div>
      <div class="form-group">
        <label class="form-label">Nota</label>
        <input class="form-control" name="note" placeholder="ex: 13º salário, venda do livro">
      </div>
      <button class="btn btn-primary w-100" style="justify-content:center;">Registrar aporte</button>
    </form>
    {% else %}
    <div class="empty-state">
      <h4>🎉 Meta atingida!</h4>
      <p>Você pode reabrir o projeto editando-o e ajustando a meta.</p>
    </div>
    {% endif %}
  </div>
</div>

<!-- Histórico -->
<div class="card">
  <div class="card-title"><h3>Histórico de aportes</h3></div>
  {% if contributions %}
  <div class="table-wrap">
    <table class="tbl">
      <thead>
        <tr>
          <th>Data</th>
          <th>Quem</th>
          <th>Nota</th>
          <th>Tipo</th>
          <th class="text-right">Valor</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {% for c in contributions %}
        <tr>
          <td class="text-dim">{{ c.contributed_at|data_br }}</td>
          <td>
            <div class="flex flex-gap">
              {% if c.user.photo %}
                <img src="{{ c.user.photo_url }}" class="avatar" style="width:24px;height:24px;">
              {% else %}
                <div class="avatar-fallback" style="width:24px;height:24px;font-size:0.75rem;">{{ c.user.full_name[0]|upper }}</div>
              {% endif %}
              <span>{{ c.user.full_name.split()[0] }}</span>
            </div>
          </td>
          <td class="text-dim">{{ c.note or '—' }}</td>
          <td>
            {% if c.is_auto %}<span class="badge badge-shared">automático</span>
            {% else %}<span class="badge badge-solo">manual</span>{% endif %}
          </td>
          <td class="text-right mono" style="color:var(--green);">{{ c.amount|brl }}</td>
          <td class="text-right">
            {% if c.user_id == current_user.id or can_edit %}
            <form method="POST" action="{{ url_for('projects.delete_contribution', project_id=project.id, contrib_id=c.id) }}"
                  style="display:inline;" onsubmit="return confirm('Remover este aporte?');">
              <button class="btn btn-danger btn-sm">×</button>
            </form>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="empty-state"><p>Nenhum aporte registrado ainda.</p></div>
  {% endif %}
</div>

{% endblock %}
