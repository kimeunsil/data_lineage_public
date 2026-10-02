CREATE PROCEDURE dw.usp_load_dim_customer
AS
BEGIN
    SET NOCOUNT ON;

    -- 변경분만 임시 테이블로
    SELECT s.customer_id, s.name, s.grade
    INTO #changed
    FROM stg.customers s
    LEFT JOIN dw.dim_customer d ON d.customer_id = s.customer_id
    WHERE d.customer_id IS NULL OR d.grade <> s.grade;

    MERGE dw.dim_customer AS T
    USING #changed AS S
       ON T.customer_id = S.customer_id
    WHEN MATCHED THEN
        UPDATE SET T.name = S.name, T.grade = S.grade, T.updated_at = GETDATE()
    WHEN NOT MATCHED THEN
        INSERT (customer_id, name, grade, updated_at) VALUES (S.customer_id, S.name, S.grade, GETDATE());

    DROP TABLE #changed;
END
GO
