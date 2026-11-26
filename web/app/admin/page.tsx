"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { adminStats, AdminStats } from "../lib/api";

export default function AdminPage() {
  const router = useRouter();
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const theme = localStorage.getItem("chatluck_theme") || "dark";
    document.documentElement.dataset.theme = theme;
    const token = localStorage.getItem("chatluck_token");
    if (!token) {
      router.replace("/login");
      return;
    }
    adminStats(token)
      .then(setStats)
      .catch((e) => setError(e instanceof Error ? e.message : "تعذر تحميل الإحصائيات"));
  }, [router]);

  return (
    <main className="admin-wrap">
      <header className="admin-head">
        <h1>لوحة تحكم المشرف</h1>
        <button className="admin-back" onClick={() => router.push("/chat")}>
          ← رجوع للمحادثة
        </button>
      </header>

      {error && (
        <div className="admin-error">
          <p>{error}</p>
          <p className="admin-error-sub">
            لوحة المشرف متاحة فقط لحسابات المشرفين المعتمدة.
          </p>
        </div>
      )}

      {!stats && !error && <p className="admin-loading">جاري تحميل الإحصائيات…</p>}

      {stats && (
        <>
          <section className="admin-cards">
            <div className="stat-card">
              <div className="stat-num">{stats.users_total}</div>
              <div className="stat-label">إجمالي المستخدمين</div>
            </div>
            <div className="stat-card">
              <div className="stat-num">{stats.users_24h}</div>
              <div className="stat-label">مستخدمون جدد (24 ساعة)</div>
            </div>
            <div className="stat-card">
              <div className="stat-num">{stats.users_7d}</div>
              <div className="stat-label">مستخدمون جدد (7 أيام)</div>
            </div>
            <div className="stat-card">
              <div className="stat-num">{stats.conversations_total}</div>
              <div className="stat-label">المحادثات</div>
            </div>
            <div className="stat-card">
              <div className="stat-num">{stats.messages_total}</div>
              <div className="stat-label">الرسائل</div>
            </div>
            <div className="stat-card">
              <div className="stat-num">{stats.messages_24h}</div>
              <div className="stat-label">رسائل (24 ساعة)</div>
            </div>
            <div className="stat-card">
              <div className="stat-num">{stats.images_total}</div>
              <div className="stat-label">صور مُنشأة</div>
            </div>
          </section>

          <section className="admin-section">
            <h2>الاستخدام حسب النموذج</h2>
            {stats.by_model.length === 0 && <p>لا توجد ردود مسجلة بعد.</p>}
            {stats.by_model.length > 0 && (
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>المزود</th>
                    <th>النموذج</th>
                    <th>عدد الردود</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.by_model.map((m, i) => (
                    <tr key={i}>
                      <td>{m.provider}</td>
                      <td>{m.model}</td>
                      <td>{m.messages}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="admin-section">
            <h2>أحدث المستخدمين</h2>
            <table className="admin-table">
              <thead>
                <tr>
                  <th>الاسم</th>
                  <th>البريد الإلكتروني</th>
                  <th>تاريخ التسجيل</th>
                </tr>
              </thead>
              <tbody>
                {stats.recent_users.map((u) => (
                  <tr key={u.email}>
                    <td>{u.display_name || "—"}</td>
                    <td>{u.email}</td>
                    <td>
                      {u.created_at
                        ? new Date(u.created_at).toLocaleString("ar-EG")
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </main>
  );
}
