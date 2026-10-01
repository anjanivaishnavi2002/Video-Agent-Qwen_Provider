function ResumeUpload({ file, setFile, allowedExtensions = [] }) {
  function handleFileChange(event) {
    const selectedFile = event.target.files?.[0];

    if (selectedFile) {
      setFile(selectedFile);
    }
  }

  const accept = allowedExtensions.join(",");
  const label = allowedExtensions
    .map((ext) => ext.replace(".", "").toUpperCase())
    .join(", ");

  return (
    <div className="upload-box">
      <input
        id="resume-input"
        type="file"
        accept={accept}
        onChange={handleFileChange}
        hidden
      />

      <label htmlFor="resume-input">
        <div className="upload-icon">📄</div>

        {file ? (
          <strong>{file.name}</strong>
        ) : (
          <>
            <strong>Upload Resume</strong>
            <span>{label}</span>
          </>
        )}
      </label>
    </div>
  );
}

export default ResumeUpload;
