import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("main", "0013_userprofile_role"),
    ]

    operations = [
        migrations.CreateModel(
            name="TeacherClassroom",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("subject", models.CharField(blank=True, default="", max_length=120)),
                ("description", models.TextField(blank=True, default="")),
                ("is_archived", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "teacher",
                    models.ForeignKey(
                        db_column="teacher_user_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="owned_teacher_classrooms",
                        to="main.userprofile",
                    ),
                ),
            ],
            options={
                "db_table": "teacher_classrooms",
                "ordering": ["-updated_at", "-created_at"],
            },
        ),
        migrations.CreateModel(
            name="ClassroomInvitation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("invited_username_snapshot", models.CharField(max_length=50)),
                ("message", models.TextField(blank=True, default="")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("accepted", "Accepted"),
                            ("declined", "Declined"),
                            ("cancelled", "Cancelled"),
                            ("revoked", "Revoked"),
                        ],
                        default="pending",
                        max_length=32,
                    ),
                ),
                ("responded_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "classroom",
                    models.ForeignKey(
                        db_column="classroom_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="invitations",
                        to="teacher_portal.teacherclassroom",
                    ),
                ),
                (
                    "invited_by",
                    models.ForeignKey(
                        db_column="invited_by_user_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sent_classroom_invitations",
                        to="main.userprofile",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        db_column="student_user_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="received_classroom_invitations",
                        to="main.userprofile",
                    ),
                ),
            ],
            options={
                "db_table": "classroom_invitations",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="ClassroomMembership",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[("accepted", "Accepted"), ("removed", "Removed")],
                        default="accepted",
                        max_length=32,
                    ),
                ),
                ("accepted_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("removed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "classroom",
                    models.ForeignKey(
                        db_column="classroom_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memberships",
                        to="teacher_portal.teacherclassroom",
                    ),
                ),
                (
                    "invitation",
                    models.ForeignKey(
                        blank=True,
                        db_column="invitation_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="memberships",
                        to="teacher_portal.classroominvitation",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        db_column="student_user_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="classroom_memberships",
                        to="main.userprofile",
                    ),
                ),
            ],
            options={
                "db_table": "classroom_memberships",
            },
        ),
        migrations.AddConstraint(
            model_name="teacherclassroom",
            constraint=models.UniqueConstraint(fields=("teacher", "name"), name="uix_tp_class_teacher_name"),
        ),
        migrations.AddIndex(
            model_name="teacherclassroom",
            index=models.Index(fields=["teacher", "is_archived"], name="idx_tp_class_teacher_active"),
        ),
        migrations.AddIndex(
            model_name="teacherclassroom",
            index=models.Index(fields=["updated_at"], name="idx_tp_class_updated"),
        ),
        migrations.AddIndex(
            model_name="classroominvitation",
            index=models.Index(fields=["classroom", "status"], name="idx_tp_inv_class_status"),
        ),
        migrations.AddIndex(
            model_name="classroominvitation",
            index=models.Index(fields=["student", "status"], name="idx_tp_inv_student_status"),
        ),
        migrations.AddIndex(
            model_name="classroominvitation",
            index=models.Index(fields=["invited_by", "created_at"], name="idx_tp_inv_teacher_time"),
        ),
        migrations.AddConstraint(
            model_name="classroommembership",
            constraint=models.UniqueConstraint(fields=("classroom", "student"), name="uix_tp_membership_class_student"),
        ),
        migrations.AddIndex(
            model_name="classroommembership",
            index=models.Index(fields=["classroom", "status"], name="idx_tp_member_class_status"),
        ),
        migrations.AddIndex(
            model_name="classroommembership",
            index=models.Index(fields=["student", "status"], name="idx_tp_member_student_status"),
        ),
    ]
