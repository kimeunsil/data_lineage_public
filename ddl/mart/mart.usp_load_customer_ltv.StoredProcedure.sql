-- 고객 생애가치(LTV) 마트 적재
-- 작성: 데이터플랫폼팀
CREATE PROCEDURE mart.usp_load_customer_ltv
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @agg TABLE (customer_key INT, total_amount DECIMAL(18, 2), order_cnt INT);

    -- 고객별 누적 매출 집계
    INSERT INTO @agg
    SELECT customer_key, SUM(amount), COUNT(*)
    FROM dw.fact_sales
    WHERE customer_key IS NOT NULL
    GROUP BY customer_key;

    TRUNCATE TABLE mart.customer_ltv;

    INSERT INTO mart.customer_ltv (customer_id, grade, total_amount, order_cnt)
    SELECT c.customer_id, c.grade, a.total_amount, a.order_cnt
    FROM @agg a
    JOIN dw.dim_customer c ON c.customer_key = a.customer_key;

    -- 동적 SQL 은 분석 대상이 아님 (문자열)
    EXEC('INSERT INTO audit.load_log SELECT ''customer_ltv'', GETDATE()');
END
GO
