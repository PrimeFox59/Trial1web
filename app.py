import streamlit as st
import json
import os
import io
import time
import pandas as pd

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# --------------------------------------------------
# Konfigurasi
SCOPES = ["https://www.googleapis.com/auth/drive"]

st.set_page_config(page_title="Streamlit + Google Drive Lokal", layout="wide")

# --------------------------------------------------
# Fungsi utilitas
def build_drive_service():
    """Load credentials dari Streamlit Secrets (untuk deploy di Streamlit Cloud)"""
    try:
        creds_dict = st.secrets["service_account"]
    except Exception:
        st.error("Secrets 'service_account' tidak ditemukan! Upload di Streamlit Cloud dashboard.")
        st.stop()
    creds = service_account.Credentials.from_service_account_info(dict(creds_dict), scopes=SCOPES)
    service = build("drive", "v3", credentials=creds)
    sa_email = creds.service_account_email
    return service, sa_email


def list_files_in_folder(service, folder_id):
    """List semua file dalam folder"""
    results = []
    page_token = None
    query = f"'{folder_id}' in parents and trashed = false"
    while True:
        resp = (
            service.files()
            .list(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType, createdTime, modifiedTime, size)",
                pageToken=page_token,
                pageSize=200,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken", None)
        if not page_token:
            break
    return results


def upload_bytes(service, folder_id, name, data_bytes, mimetype="application/octet-stream"):
    media = MediaIoBaseUpload(io.BytesIO(data_bytes), mimetype=mimetype, resumable=True)
    file_metadata = {"name": name, "parents": [folder_id]}
    try:
        created = (
            service.files().create(body=file_metadata, media_body=media, fields="id").execute()
        )
        return created.get("id")
    except Exception as e:
        # Tangani error storageQuotaExceeded (service account tidak bisa upload ke My Drive)
        if hasattr(e, 'status_code') and e.status_code == 403 and 'storageQuotaExceeded' in str(e):
            st.error("GAGAL UPLOAD: Service Account tidak bisa upload ke My Drive. Gunakan Shared Drive (Drive Bersama) dan pastikan folder ID berasal dari Shared Drive yang sudah di-share ke Service Account!")
        else:
            st.error(f"Gagal upload: {e}")
        return None


def download_file_bytes(service, file_id):
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    fh.seek(0)
    return fh.read()


def update_file_bytes(service, file_id, data_bytes, mimetype="application/json"):
    media = MediaIoBaseUpload(io.BytesIO(data_bytes), mimetype=mimetype, resumable=True)
    updated = service.files().update(fileId=file_id, media_body=media).execute()
    return updated


def delete_file(service, file_id):
    try:
        service.files().delete(fileId=file_id, supportsAllDrives=True).execute()
    except Exception as e:
        if hasattr(e, 'status_code') and e.status_code == 404:
            st.error(f"File tidak ditemukan atau sudah dihapus (ID: {file_id})")
        else:
            st.error(f"Gagal menghapus file: {e}")


# --------------------------------------------------
# UI
st.title("📂 Streamlit + Google Drive (Lokal Dev Version)")

service, sa_email = build_drive_service()

with st.expander("Instruksi"):
    st.markdown(
        """
        1. Pastikan file `service_account.json` ada di folder project.
        2. **WAJIB:** Gunakan folder dari Shared Drive (Drive Bersama), BUKAN dari My Drive pribadi!
        3. Share folder Shared Drive ke email service account berikut:
        """
    )
    st.code(sa_email)
    st.markdown(
        """
        4. Copy-paste **Folder ID** dari Shared Drive ke input di bawah ini.
        
        > Service Account TIDAK BISA upload ke My Drive. Hanya bisa ke Shared Drive yang sudah di-share ke Service Account.
        """
    )

folder_id = st.text_input("Masukkan Google Drive Folder ID:")
if not folder_id:
    st.warning("Masukkan Folder ID dulu (contoh: 1k8x-xxx...)")
    st.stop()

tabs = st.tabs(["List", "Create record", "Upload file", "Edit record", "Download", "Delete"])

# --------------------------------------------------
# Tab List
with tabs[0]:
    st.header("Daftar File di Folder")
    with st.spinner("Mengambil data..."):
        files = list_files_in_folder(service, folder_id)
    if not files:
        st.info("Folder kosong atau belum di-share ke service account.")
    else:
        df = pd.DataFrame(files)

        def nice_size(s):
            try:
                s = int(s)
            except Exception:
                return "-"
            for unit in ["B", "KB", "MB", "GB"]:
                if s < 1024:
                    return f"{s}{unit}"
                s = s // 1024
            return f"{s}TB"

        if "size" in df.columns:
            df["size"] = df["size"].apply(nice_size)

        st.dataframe(df[["name", "id", "mimeType", "createdTime", "modifiedTime", "size"]])

# --------------------------------------------------
# Tab Create record
with tabs[1]:
    st.header("Buat Record JSON")
    title = st.text_input("Judul", value="record")
    description = st.text_area("Deskripsi")
    if st.button("Buat JSON Record"):
        record = {
            "title": title,
            "description": description,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        payload = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
        name = f"record_{int(time.time())}_{title}.json"
        fid = upload_bytes(service, folder_id, name, payload, mimetype="application/json")
        st.success(f"Record berhasil dibuat (ID: {fid})")
        st.json(record)

# --------------------------------------------------
# Tab Upload file
with tabs[2]:
    st.header("Upload File")
    uploaded = st.file_uploader("Pilih file", type=None)
    if uploaded and st.button("Upload ke Drive"):
        data = uploaded.read()
        fid = upload_bytes(
            service, folder_id, uploaded.name, data, mimetype=uploaded.type or "application/octet-stream"
        )
        st.success(f"File terupload ke Drive (ID: {fid})")

# --------------------------------------------------
# Tab Edit record
with tabs[3]:
    st.header("Edit Record JSON")
    files_all = list_files_in_folder(service, folder_id)
    json_files = [f for f in files_all if f["name"].lower().endswith(".json")]
    if not json_files:
        st.info("Tidak ada file JSON di folder.")
    else:
        sel = st.selectbox("Pilih file JSON", [f"{f['name']} ({f['id']})" for f in json_files])
        if sel and st.button("Load file"):
            fid = sel.split("(")[-1].strip(")")
            raw = download_file_bytes(service, fid)
            obj = json.loads(raw.decode("utf-8"))
            edited = st.text_area("Edit JSON", value=json.dumps(obj, indent=2, ensure_ascii=False), height=300)
            if st.button("Simpan perubahan"):
                newobj = json.loads(edited)
                update_file_bytes(service, fid, json.dumps(newobj, indent=2, ensure_ascii=False).encode("utf-8"))
                st.success("File berhasil diupdate")

# --------------------------------------------------
# Tab Download
with tabs[4]:
    st.header("Download File")
    files_all = list_files_in_folder(service, folder_id)
    if files_all:
        sel = st.selectbox("Pilih file", [f"{f['name']} ({f['id']})" for f in files_all])
        if sel and st.button("Download file"):
            fid = sel.split("(")[-1].strip(")")
            data = download_file_bytes(service, fid)
            name = next((f["name"] for f in files_all if f["id"] == fid), "download.bin")
            st.download_button("Klik untuk download", data=data, file_name=name)
    else:
        st.info("Folder kosong.")

# --------------------------------------------------
# Tab Delete
with tabs[5]:
    st.header("Hapus File")
    files_all = list_files_in_folder(service, folder_id)
    if files_all:
        sel = st.selectbox("Pilih file untuk hapus", [f"{f['name']} ({f['id']})" for f in files_all])
        if sel and st.button("Hapus file"):
            fid = sel.split("(")[-1].strip(")")
            delete_file(service, fid)
            st.success("File berhasil dihapus")
    else:
        st.info("Folder kosong.")
