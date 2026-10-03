"""Explicit database migration and account initialization; never runs at web startup."""
import argparse
from getpass import getpass
import os
from pathlib import Path
import re
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select

from .config import get_settings
from .database import create_database
from .models import CatalogItem, Department, LoginSession, Package, User
from .security import hash_password
from .services import audit


def seed_catalog(db):
    internal = db.scalar(select(Department).where(Department.name == "内科"))
    lab = db.scalar(select(Department).where(Department.name == "检验科"))
    if internal is None:
        internal = Department(name="内科")
        db.add(internal)
    if lab is None:
        lab = Department(name="检验科")
        db.add(lab)
    db.flush()
    if db.scalar(select(Package).where(Package.name == "基础体检套餐（演示）")) is None:
        package = Package(name="基础体检套餐（演示）", description="用于验证基础流程；实际套餐和参考范围需由体检中心确认。")
        package.items = [CatalogItem(name="内科检查", department_id=internal.id, unit="", reference_range="由医生填写检查所见"), CatalogItem(name="血常规", department_id=lab.id, unit="", reference_range="以实验室提供的参考范围为准"), CatalogItem(name="空腹血糖", department_id=lab.id, unit="mmol/L", reference_range="以实验室提供的参考范围为准")]
        db.add(package)
    return internal, lab


def add_user(db, username, display_name, role, password, department_id=None):
    user = db.scalar(select(User).where(User.username == username))
    if user is not None:
        return user
    user = User(username=username, display_name=display_name, role=role, department_id=department_id, password_hash=hash_password(password))
    db.add(user)
    db.flush()
    audit(db, user, "user.initialize", "user", user.id)
    return user


def seed_demo(db, password: str, username="admin"):
    """Caller owns transaction; all demo credentials are explicitly supplied by the operator."""
    internal, lab = seed_catalog(db)
    add_user(db, username, "系统管理员", "admin", password)
    add_user(db, "frontdesk", "演示登记员", "registrar", password)
    add_user(db, "doctor_internal", "演示内科医生", "doctor", password, internal.id)
    add_user(db, "doctor_lab", "演示检验医生", "doctor", password, lab.id)
    add_user(db, "reviewer", "演示总检医生", "reviewer", password)


def read_password(env_name):
    if env_name:
        password = os.environ.get(env_name)
        if password is None:
            raise ValueError("指定的密码环境变量不存在")
    else:
        password = getpass("请输入密码（至少12个字符）：")
        if password != getpass("再次输入密码："):
            raise ValueError("两次输入的密码不一致")
    if not 12 <= len(password) <= 1024:
        raise ValueError("密码长度必须为12～1024个字符")
    return password


def migrate_database(database_url):
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")


def main(argv=None):
    parser = argparse.ArgumentParser(description="体检数据库与账号管理")
    subcommands = parser.add_subparsers(dest="action", required=True)
    init = subcommands.add_parser("init", help="升级数据库并初始化管理员")
    init.add_argument("--username", default="admin")
    init.add_argument("--demo", action="store_true", help="建立五个演示角色账号与演示套餐")
    init.add_argument("--catalog", action="store_true", help="建立演示科室和套餐；不创建其他演示账号")
    init.add_argument("--password-env", help="读取指定环境变量中的密码，禁止明文命令行密码")
    subcommands.add_parser("migrate", help="只升级数据库结构")
    change = subcommands.add_parser("set-password", help="设置账号新密码并撤销其所有登录会话")
    change.add_argument("--username", required=True)
    change.add_argument("--password-env")
    create = subcommands.add_parser("create-user", help="建立独立账号；医生必须分配科室")
    create.add_argument("--username", required=True)
    create.add_argument("--display-name", required=True)
    create.add_argument("--role", required=True, choices=["admin", "registrar", "doctor", "reviewer"])
    create.add_argument("--department-id", type=int)
    create.add_argument("--password-env")
    args = parser.parse_args(argv)
    settings = get_settings()
    engine = None
    try:
        if args.action in {"init", "set-password", "create-user"}:
            if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", args.username):
                raise ValueError("账号名应为3～64位英文字母、数字、点、下划线或连字符")
            if args.action == "init" and args.demo and args.username in {"frontdesk", "doctor_internal", "doctor_lab", "reviewer"}:
                raise ValueError("管理员账号名不能与演示账号重名")
            password = read_password(args.password_env)
        migrate_database(settings.resolved_database_url)
        if args.action == "migrate":
            print("数据库已升级。")
            return 0
        engine, Session = create_database(settings.resolved_database_url)
        success_messages = []
        with Session.begin() as db:
            if args.action == "init":
                if args.demo:
                    seed_demo(db, password, args.username)
                else:
                    if args.catalog:
                        seed_catalog(db)
                    add_user(db, args.username, "系统管理员", "admin", password)
                (settings.reports_dir or settings.data_dir / "reports").mkdir(parents=True, exist_ok=True)
                success_messages.append("初始化完成；已有账号的密码不会被覆盖。")
                if args.demo:
                    success_messages.append("演示账号：" + args.username + "、frontdesk、doctor_internal、doctor_lab、reviewer。使用本人本次设定的密码；正式使用前逐人设置独立密码。")
            elif args.action == "set-password":
                user = db.scalar(select(User).where(User.username == args.username))
                if user is None:
                    raise ValueError("账号不存在")
                user.password_hash = hash_password(password)
                db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
                audit(db, user, "user.password_reset_cli", "user", user.id)
                success_messages.append("密码已更新，该账号原登录会话已撤销。")
            else:
                if db.scalar(select(User).where(User.username == args.username)):
                    raise ValueError("账号已存在")
                if not args.display_name.strip() or len(args.display_name) > 100:
                    raise ValueError("显示名称应为1～100个字符")
                if args.role == "doctor":
                    if not args.department_id or db.get(Department, args.department_id) is None:
                        raise ValueError("医生必须指定有效的--department-id")
                elif args.department_id is not None:
                    raise ValueError("仅医生账号需要指定科室")
                add_user(db, args.username, args.display_name.strip(), args.role, password, args.department_id)
                success_messages.append("账号已创建。")
        for message in success_messages:
            print(message)
        return 0
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        # Database driver exception strings may contain patient values or credentials.
        print("初始化失败，请检查数据库配置与服务状态（" + type(exc).__name__ + "）。", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
