from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from app import db
from app.models import Project, ProjectMember, Contribution, SubProject, User

projects_bp = Blueprint("projects", __name__)


def _parse_decimal(s):
    if not s:
        return None
    try:
        return Decimal(str(s).replace(".", "").replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return None


def _user_can_view(project):
    return (project.owner_id == current_user.id
            or current_user.id in project.member_ids()
            or current_user.is_admin)


def _user_can_edit(project):
    return project.owner_id == current_user.id or current_user.is_admin


@projects_bp.route("/")
@login_required
def list_projects():
    member_ids = [m.project_id for m in
                  ProjectMember.query.filter_by(user_id=current_user.id).all()]
    projects = Project.query.filter(
        (Project.owner_id == current_user.id) | (Project.id.in_(member_ids))
    ).order_by(Project.is_completed, Project.created_at.desc()).all()
    return render_template("projects/list.html", projects=projects)


@projects_bp.route("/novo", methods=["GET", "POST"])
@login_required
def new_project():
    users = User.query.filter(User.id != current_user.id).order_by(User.full_name).all()
    if request.method == "POST":
        return _save_project(None, users)
    return render_template("projects/form.html", project=None, users=users)


@projects_bp.route("/<int:project_id>")
@login_required
def detail_project(project_id):
    p = Project.query.get_or_404(project_id)
    if not _user_can_view(p):
        abort(403)
    contribs = Contribution.query.filter_by(project_id=p.id)\
        .order_by(Contribution.contributed_at.desc()).all()

    # Histórico por usuário
    user_totals = {}
    for c in contribs:
        uid = c.user_id
        if uid not in user_totals:
            user_totals[uid] = {"user": c.user, "total": 0, "count": 0}
        user_totals[uid]["total"] += float(c.amount)
        user_totals[uid]["count"] += 1

    return render_template("projects/detail.html",
                           project=p,
                           contributions=contribs,
                           user_totals=list(user_totals.values()),
                           can_edit=_user_can_edit(p))


@projects_bp.route("/<int:project_id>/editar", methods=["GET", "POST"])
@login_required
def edit_project(project_id):
    p = Project.query.get_or_404(project_id)
    if not _user_can_edit(p):
        abort(403)
    users = User.query.filter(User.id != current_user.id).order_by(User.full_name).all()
    if request.method == "POST":
        return _save_project(p, users)
    return render_template("projects/form.html", project=p, users=users)


def _save_project(project, users):
    name = request.form.get("name", "").strip()
    desc = request.form.get("description", "").strip()
    target = _parse_decimal(request.form.get("target_amount")) or Decimal("0")
    monthly_auto = _parse_decimal(request.form.get("monthly_auto")) or Decimal("0")
    auto_day = request.form.get("auto_day", "1")
    deadline_str = request.form.get("deadline")

    # Subprojetos enviados como arrays
    sub_names = request.form.getlist("sub_name")
    sub_descs = request.form.getlist("sub_description")
    sub_amounts = request.form.getlist("sub_amount")

    if not name:
        flash("Nome é obrigatório.", "danger")
        return render_template("projects/form.html", project=project, users=users)

    # Validar subprojetos: filtrar os com nome E valor preenchidos
    valid_subs = []
    for i, sn in enumerate(sub_names):
        sn = sn.strip()
        amt = _parse_decimal(sub_amounts[i] if i < len(sub_amounts) else "")
        if sn and amt and amt > 0:
            valid_subs.append({
                "name": sn,
                "description": (sub_descs[i] if i < len(sub_descs) else "").strip(),
                "amount": amt,
            })

    # Se não há subs nem target, exige um deles
    if not valid_subs and target <= 0:
        flash("Defina ao menos uma meta total OU subprojetos.", "danger")
        return render_template("projects/form.html", project=project, users=users)

    try:
        auto_day = max(1, min(28, int(auto_day)))
    except ValueError:
        auto_day = 1

    deadline = None
    if deadline_str:
        try:
            deadline = datetime.strptime(deadline_str, "%Y-%m-%d").date()
        except ValueError:
            pass

    if project is None:
        project = Project(owner_id=current_user.id)
        db.session.add(project)

    project.name = name
    project.description = desc
    # Se há subs, target_amount fica como referência; computed_target usa a soma
    project.target_amount = target if not valid_subs else sum(s["amount"] for s in valid_subs)
    project.monthly_auto = monthly_auto
    project.auto_day = auto_day
    project.deadline = deadline

    db.session.flush()

    # Subprojetos: limpa e recria
    SubProject.query.filter_by(project_id=project.id).delete()
    for idx, s in enumerate(valid_subs):
        db.session.add(SubProject(
            project_id=project.id,
            name=s["name"],
            description=s["description"],
            target_amount=s["amount"],
            order_index=idx,
        ))

    # Membros
    ProjectMember.query.filter_by(project_id=project.id).delete()
    # Owner sempre é membro
    owner_share = _parse_decimal(request.form.get(f"member_share_{current_user.id}")) or Decimal("0")
    db.session.add(ProjectMember(project_id=project.id, user_id=current_user.id,
                                 monthly_share=owner_share))

    for u in users:
        if request.form.get(f"member_{u.id}"):
            ms = _parse_decimal(request.form.get(f"member_share_{u.id}")) or Decimal("0")
            db.session.add(ProjectMember(project_id=project.id, user_id=u.id,
                                         monthly_share=ms))

    db.session.commit()
    flash("Projeto salvo.", "success")
    return redirect(url_for("projects.detail_project", project_id=project.id))


@projects_bp.route("/<int:project_id>/aporte", methods=["POST"])
@login_required
def add_contribution(project_id):
    p = Project.query.get_or_404(project_id)
    if not _user_can_view(p):
        abort(403)
    amount = _parse_decimal(request.form.get("amount"))
    note = request.form.get("note", "").strip()
    d_str = request.form.get("contributed_at")
    if not amount or amount <= 0:
        flash("Informe um valor válido.", "danger")
        return redirect(url_for("projects.detail_project", project_id=p.id))
    try:
        d = datetime.strptime(d_str, "%Y-%m-%d").date() if d_str else date.today()
    except ValueError:
        d = date.today()
    c = Contribution(project_id=p.id, user_id=current_user.id,
                     amount=amount, note=note, contributed_at=d)
    db.session.add(c)

    # Marca como completo se atingiu meta
    db.session.flush()
    if p.total_raised >= p.computed_target:
        p.is_completed = True

    db.session.commit()
    flash("Aporte registrado!", "success")
    return redirect(url_for("projects.detail_project", project_id=p.id))


@projects_bp.route("/<int:project_id>/aporte/<int:contrib_id>/excluir", methods=["POST"])
@login_required
def delete_contribution(project_id, contrib_id):
    p = Project.query.get_or_404(project_id)
    c = Contribution.query.get_or_404(contrib_id)
    if c.user_id != current_user.id and not _user_can_edit(p):
        abort(403)
    db.session.delete(c)
    if p.total_raised < p.computed_target:
        p.is_completed = False
    db.session.commit()
    flash("Aporte removido.", "info")
    return redirect(url_for("projects.detail_project", project_id=p.id))


@projects_bp.route("/<int:project_id>/excluir", methods=["POST"])
@login_required
def delete_project(project_id):
    p = Project.query.get_or_404(project_id)
    if not _user_can_edit(p):
        abort(403)
    db.session.delete(p)
    db.session.commit()
    flash("Projeto removido.", "info")
    return redirect(url_for("projects.list_projects"))
